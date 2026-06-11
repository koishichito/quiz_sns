"""認証まわり: Keycloakが発行したトークンの検証と「現在のユーザー」依存関係。

━━ 認証の構造(自前認証 → Keycloak委譲)━━━━━━━━━━━━━━━━
以前はこのアプリ自身がパスワードを預かり、トークンを発行していた
(認証サーバとAPIサーバの一人二役)。Keycloak 導入後の役割分担:

  Keycloak(IdP: Identity Provider)
    ユーザー登録・パスワード保管・ログイン・トークン発行をすべて担当。
  このアプリ(リソースサーバ)
    届いたトークンを【検証するだけ】。パスワードにはもう一切触らない
    (bcrypt も /auth/register も /auth/login もアプリから消えた)。

  [ログイン]  クライアント ──(username/password)──▶ Keycloak
              クライアント ◀──(アクセストークン JWT)── Keycloak
  [以降毎回]  クライアント ──(Authorization: Bearer <token>)──▶ このアプリ
              このアプリは Keycloak の【公開鍵】で署名を検証して受け入れる。
              検証のたびに Keycloak へ問い合わせるわけではない(下記JWKS参照)。

━━ HS256 → RS256(共通鍵 → 公開鍵方式)━━━━━━━━━━━━━━━━
自前認証時代は HS256(共通鍵)だった。発行者と検証者が同一サーバなら、
1本の SECRET_KEY で署名も検証もできて十分。
いまは発行者(Keycloak)と検証者(このアプリ)が別プロセスになった。
共通鍵を両者で共有すると「検証できる者は誰でも偽造もできる」ことになる。
そこで RS256(公開鍵方式):
  署名 = Keycloak だけが持つ【秘密鍵】で行う → 偽造できるのはKeycloakだけ
  検証 = 公開してよい【公開鍵】で行う     → 漏れても偽造には使えない
検証側のサービスが何個に増えても安全性が落ちない。複数のサービスが
1つのIdPを共有する構成(SSO)の土台がこれ。

━━ JWKS(JSON Web Key Set)と kid ━━━━━━━━━━━━━━━━━━━━
では公開鍵をどう入手するか。Keycloak は
  {SERVER_URL}/realms/{realm}/protocol/openid-connect/certs
で公開鍵の一覧をJSON(JWKS)として配布している。鍵は複数並びうる
(鍵ローテーション中は新旧が共存する)ため、トークンのヘッダにある
kid(Key ID)と突き合わせて「この署名に対応する鍵」を選ぶ。
このHTTP取得・kid照合・キャッシュを PyJWT の PyJWKClient が全部やってくれる。

━━ JITプロビジョニング(Just-In-Time)━━━━━━━━━━━━━━━━━
ユーザーの本体(パスワード等)は Keycloak 側にあるが、quizzes.owner_id の
ような外部キーの参照先として、アプリのDB側にも users 行が必要。
そこで「検証済みトークンを初めて持ってきた人の行を、その場で作る」方式を
採る(crud.get_or_create_user)。事前にユーザー一覧を同期するバッチが不要に
なり、Keycloak側でユーザーが増えてもアプリは何もしなくてよい。
"""
import jwt  # パッケージ名は PyJWT、import 名は jwt。
            # 紛らわしい別物(python-jwt 等)があるので pip install は PyJWT を指定
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import crud, models
from .config import settings
from .database import get_db

ALGORITHM = "RS256"  # 公開鍵方式(RSA + SHA-256)。Keycloakのアクセストークンの既定。
                     # 自前発行時代の HS256(共通鍵)から変えた理由は冒頭の解説を参照

# oauth2_scheme: Authorization: Bearer <token> ヘッダから <token> を抜き出す部品。
# ヘッダが無い/形式が違えばその場で 401(エンドポイントは実行されない)。
# tokenUrl は通信処理には関与しない【ドキュメント情報】だが、いまや
# アプリ自身のURLではなく Keycloak のトークンエンドポイントを指している点に注目。
# Swagger UI(/docs)の Authorize ボタンはこのURLへ直接 username/password を
# 送ってトークンを得る(= ログインの宛先がアプリの外であることがUIにも現れる)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=settings.keycloak_token_url)

# JWKSクライアント(公開鍵の取得係)。モジュール読み込み時に1個だけ作って使い回す。
# この時点ではHTTPアクセスは発生しない: 初回の検証時に取得し、以後はキャッシュが
# 効く(cache_keys=True)ので、毎リクエスト Keycloak を叩くわけではない。
# テストではこの変数ごと偽物に差し替える(tests/test_api.py 参照)――
# 「外部との境界をモジュール変数に切り出しておくと差し替えやすい」という設計でもある
jwks_client = jwt.PyJWKClient(settings.keycloak_jwks_url, cache_keys=True)


def get_current_user(
    token: str = Depends(oauth2_scheme),  # ヘッダから Bearer トークンを抽出。
                                          # ヘッダ欠如はここで401になり、以降は実行されない
    db: Session = Depends(get_db),
    # ↑ エンドポイント側も Depends(get_db) を書いているが、FastAPI は同一
    #   リクエスト内で同じ依存関係の結果をキャッシュするため、ここで受け取る db と
    #   エンドポイントが受け取る db は【同一のセッション】(従来と同じ仕組み)
) -> models.User:
    """トークン → User の復元。認証が必要な全エンドポイントの共通部品。

    自前認証時代と関数名・使い方(Depends で注入)は同一のまま、中身だけが
    「自分の共通鍵で検証」→「Keycloakの公開鍵で検証 + JITプロビジョニング」
    に入れ替わった。利用側(routers/)は1文字も変更されていない ――
    依存関係の【中身】を差し替えても【契約】が同じなら呼び出し側は無傷、
    という依存性注入の利点が、アーキテクチャ変更のスケールで効いた例。
    """
    # 失敗経路が複数あるので、例外オブジェクトを先に1個作って使い回す
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="認証情報が無効です",
        # WWW-Authenticate: Bearer は「Bearer方式で認証し直せ」と伝える
        # HTTP仕様(RFC 6750)上のお作法ヘッダ。401を返すときに付けるのが正式
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # (1) トークンヘッダの kid を読み、対応する公開鍵をJWKSから選ぶ
        #     (キャッシュに無ければ、ここで Keycloak へのHTTPアクセスが走る)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        # (2) 署名・期限・発行者を一括検証してペイロードを取り出す
        payload = jwt.decode(
            token,
            signing_key.key,
            # algorithms をこちらで明示するのはセキュリティ上必須(従来と同じ理由):
            # トークン側の自称アルゴリズムを信用すると、弱い方式へすり替える
            # 「アルゴリズム混乱攻撃」の余地が生まれる
            algorithms=[ALGORITHM],
            # iss(発行者)が、うちが信用する realm と厳密一致するかの検証。
            # これが無いと「別realm(=別の管理者の世界)の正規トークン」まで
            # このAPIを通れてしまう
            issuer=settings.keycloak_issuer,
            # aud(宛先)の検証は設定がある場合のみ有効化。
            # Keycloak は既定では aud にこのAPIの名前を入れない(クライアント側の
            # マッパー設定に依存)ため、配られた環境に合わせて切り替えられる形
            audience=settings.keycloak_audience,
            options={
                "verify_aud": settings.keycloak_audience is not None,
                # exp と sub が【存在すること】自体も要求する。
                # 「expなしトークン」が永久に有効になる事故をここで塞ぐ
                "require": ["exp", "sub"],
            },
        )
    except jwt.PyJWTError:
        # 署名不正・期限切れ・発行者不一致・形式不正・JWKS取得失敗が全部ここに来る
        # (PyJWKClientError も PyJWTError の子クラス)。
        # 失敗理由を細かくクライアントへ返さないのは従来と同じ方針
        # (正規ユーザーには無意味で、攻撃者へのヒントにしかならない)
        raise credentials_error

    # sub = Keycloak上の不変のユーザーID(UUID文字列)。
    # preferred_username は人間向けの表示名で、Keycloak側で変更されうる。
    # だからDBとの紐付けキーは必ず sub にする(username で紐付けると、
    # 改名しただけで別人扱いになってしまう)
    username = payload.get("preferred_username") or payload["sub"]
    return crud.get_or_create_user(db, keycloak_sub=payload["sub"], username=username)
