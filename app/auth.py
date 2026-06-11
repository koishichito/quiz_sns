"""認証まわり: パスワードハッシュ化・JWT発行・「現在のユーザー」依存関係。

━━ 全体の流れ(トークン認証のライフサイクル)━━━━━━━━━━━━━━
  [登録]     平文パスワード → hash_password → DBには hashed_password を保存
  [ログイン] 平文パスワード → verify_password で照合 → 成功なら
             create_access_token で JWT を発行してクライアントへ渡す
  [以降毎回] クライアントは Authorization: Bearer <JWT> ヘッダを付けて送る
             → get_current_user がトークンを検証して User を復元する

サーバ側はログイン状態を一切記憶しない(ステートレス)。
「誰か」の証明は毎リクエスト、トークンの署名検証だけで完結する。
これがセッション方式と対比したときのJWT方式の本質。

━━ JWT(JSON Web Token)とは ━━━━━━━━━━━━━━━━━━━━━━━━
  xxxxx.yyyyy.zzzzz というドット区切り3部構成の文字列。
    xxxxx = ヘッダ(アルゴリズム情報)の base64url
    yyyyy = ペイロード(sub=主体, exp=期限 などの「クレーム」)の base64url
    zzzzz = 前2つを SECRET_KEY で署名(HMAC-SHA256)したもの
  最重要: base64 は【暗号化ではない】。ペイロードは誰でもデコードして読める。
  守られているのは機密性ではなく完全性 ―― 1文字でも改竄すれば署名が
  合わなくなるので、サーバは「自分が発行したまま無傷である」ことを検証できる。
  だからペイロードに秘密情報を入れてはいけない(user_id 程度に留める)。

━━ get_current_user = 依存性注入の代表例その2 ━━━━━━━━━━━━
「ヘッダのトークンを検証し、対応する User をDBから引いて返す」という
横断的処理を、必要なエンドポイントが Depends で1行宣言するだけで再利用できる。
しかも get_current_user 自身が oauth2_scheme と get_db に Depends している
= 依存関係は入れ子にでき、全体が木構造(依存ツリー)を成す。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- plain.encode("utf-8") / .decode("utf-8")
    str(文字列)と bytes(バイト列)の相互変換。bcrypt はバイト列しか
    受け取らないため変換が要る。DBには str で保存したいので戻すとき decode。
- datetime.now(timezone.utc)
    タイムゾーン付きの現在時刻。JWT の exp はUTC基準のUNIX時間で扱われる
    ので、ローカル時刻が混ざる naive な datetime.now() ではなくUTCを明示する。
- timedelta(minutes=...) は「時間の差分」。datetime に加算できる。
- except (A, B, C): 複数の例外型をタプルでまとめて捕まえる書き方。
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt  # パッケージ名は PyJWT、import 名は jwt。
            # 紛らわしい別物(python-jwt 等)があるので pip install は PyJWT を指定
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db

ALGORITHM = "HS256"  # HMAC-SHA256 = 共通鍵方式。発行者と検証者が同一サーバの
                     # 本構成ではこれで十分(検証だけ他サービスへ配りたく
                     # なったら公開鍵方式 RS256 等を検討する)

# OAuth2PasswordBearer のインスタンスは「呼び出し可能オブジェクト」で、
# Depends に渡すと毎リクエストこう働く:
#   Authorization: Bearer <token> ヘッダから <token> 部分を抜き出して返す。
#   ヘッダが無い/形式が違えば、その場で 401 を返す(エンドポイントは実行されない)。
# tokenUrl="/auth/login" は実際の通信処理には関与しない【ドキュメント情報】。
# Swagger UI(/docs)がこれを読んで Authorize ボタンを生成し、
# 「トークンはここで取得できる」と知る。おかげで /docs 上で
# ログイン → 認証付きAPIの試行、が完結する
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(plain: str) -> str:
    """平文パスワードを bcrypt でハッシュ化する。

    bcrypt を使う理由(SHA-256 単発ではダメな理由):
    (1) わざと遅い: gensalt のコスト係数に応じて内部で 2^n 回反復する設計。
        DBが漏洩しても、総当たりでの復元を計算量的に非現実的にする。
    (2) ソルト内蔵: gensalt() が毎回ランダムなソルトを生成し、結果文字列に
        埋め込む。同じパスワードでも毎回違うハッシュになるため、
        事前計算表(レインボーテーブル)攻撃が無効化される。
    出力例: $2b$12$<ソルト22文字><ハッシュ31文字>
            形式バージョン・コスト・ソルトが全部この1本に入っている。
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """照合。checkpw が hashed からソルトとコストを取り出し、plain を
    同条件でハッシュし直して比較する(タイミング攻撃耐性のある比較)。

    『保存ハッシュを復号して平文と比べる』のでは【ない】点に注意。
    bcrypt は一方向関数で、復号は管理者にも誰にもできない。
    だから「パスワードを忘れた」への正しい答えは再設定であって照会ではない。
    """
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    """user_id を主体(sub)とする署名付きトークンを発行する。"""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    # sub(subject)= このトークンが誰のものか。
    #   JWT仕様(RFC 7519)上、sub は文字列でなければならない。
    #   PyJWT 2.10 以降は int を入れるとデコード時に弾かれるため str() を通す。
    # exp(expiration)= 期限。jwt.decode が現在時刻と自動比較し、
    #   過ぎていれば ExpiredSignatureError を投げる(自前の期限比較は不要)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def get_current_user(
    # ━ 依存関係が依存関係を持つ(入れ子)━
    token: str = Depends(oauth2_scheme),  # ヘッダから Bearer トークンを抽出。
                                          # ヘッダ欠如はここで401になり、以降は実行されない
    db: Session = Depends(get_db),
    # ↑ エンドポイント側も Depends(get_db) を書いているが、FastAPI は同一
    #   リクエスト内で同じ依存関係の結果をキャッシュする(use_cache=True が既定)。
    #   よって get_db の実行は1リクエストに1回だけで、ここで受け取る db と
    #   エンドポイントが受け取る db は【同一のセッション】。
    #   同じトランザクションの一貫した世界を見ることが保証される
) -> models.User:
    """トークン → User の復元。認証が必要な全エンドポイントの共通部品。

    Depends(get_current_user) と書いたエンドポイントは、この関数が
    return した User を引数に受け取る。途中で raise された場合は
    エンドポイント本体が一度も実行されずに 401 が返る。
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
        # decode は復元と同時に署名検証・exp検証も行う。
        # 改竄・期限切れはここで例外として現れる。
        # algorithms を明示的にリストで渡すのはセキュリティ上必須:
        # 検証側がアルゴリズムをトークン側の自称に任せると、
        # alg を弱い方式へすり替える「アルゴリズム混乱攻撃」の余地が生まれる
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        # InvalidTokenError: 改竄・期限切れ・形式不正
        #   (ExpiredSignatureError もこの子クラスなのでまとめて捕まる)
        # KeyError: sub クレームが無い / ValueError: sub が数値に変換できない
        # 失敗理由を細かく区別してクライアントへ返す必要はない
        # (正規ユーザーには無意味で、攻撃者へのヒントにしかならない)
        raise credentials_error
    user = db.get(models.User, user_id)
    if user is None:
        # トークン自体は正規だが、ユーザーが既に削除されている等のケース
        raise credentials_error
    return user
