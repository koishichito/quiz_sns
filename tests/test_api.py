"""結合テスト: 本物のHTTPリクエストの形でAPI全体を通しで検証する。

━━ 外部依存を【2つとも】偽物に差し替える ━━━━━━━━━━━━━━━━
本番では MySQL と Keycloak という2つの外部プロセスに依存するが、
テストでは両方を差し替えて、ネットワーク無しで全経路を検証する。

(1) DB: MySQL → インメモリSQLite
    app.dependency_overrides[get_db] の【1行】で、アプリ本体のコードを
    1文字も変えずに差し替わる。依存性注入の最大の見せ場(従来どおり)。

(2) 認証: Keycloak → テストが自作したRSA鍵ペア
    テストがRSA鍵ペアを生成し、「秘密鍵で署名したトークン」を自分で作り、
    「公開鍵を返す偽JWKSクライアント」を app.auth.jwks_client へ差し込む。
    つまり【テストがKeycloakに成り代わる】。
    重要なのは、署名・期限・発行者(iss)の検証コードは本物のまま通ること。
    get_current_user を丸ごと差し替える方式だと、検証ロジックが一切
    テストされなくなる。偽物にするのは「外部との境界(公開鍵の入手)」
    だけに絞る ―― どこを切ってモックするかが、モック設計の核心。

実行: プロジェクトルートで  pytest tests/ -q

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- uuid.uuid5(名前空間, 名前)
    名前から決定的に作るUUID(同じ入力なら必ず同じ値)。乱数版の uuid4 と
    違い、テスト中「同じ username = 同じ sub = 同じ人物」を再現できる。
- {**SAMPLE_QUIZ, "answer_index": 99}
    dict のコピー+一部キーの上書き(マージ記法)。元の dict は変更されない。
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

# ━ app パッケージを import する【前】に環境変数を立てる(この順序が重要)━
# config.py は import された瞬間に Settings() を実行して環境変数 / .env を読む。
# 万一にも本物のMySQL・本物のKeycloakを参照しないよう、無害な値で塞いでおく保険。
# setdefault = 「未設定のときだけセット」(既にある設定は尊重する)
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("KEYCLOAK_SERVER_URL", "http://keycloak.test")
os.environ.setdefault("KEYCLOAK_REALM", "quiz-sns-test")

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth as auth_module
from app.config import settings
from app.database import Base, get_db
from app.main import app

# ---- テスト専用DB: インメモリSQLite ----
# "sqlite://"(パス無し)= ファイルを作らずメモリ上にDBを構築。プロセス終了で消える。
# ただし罠が2つあり、それぞれ引数で潰す:
#   (1) SQLiteのメモリDBは【接続ごとに別世界】になる仕様。プールが接続を
#       2本張ると、片方で作ったテーブルがもう片方には存在しない。
#       → StaticPool で「全員が同じ1本の接続を共有」させて回避
#   (2) SQLiteは既定で「接続を作ったスレッド以外からの利用」を禁止する。
#       TestClient はリクエストを別スレッドで処理するため、これに抵触する。
#       → check_same_thread=False でそのチェックを外す
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},  # ドライバ(sqlite3)へ直接渡す引数
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False)

# テスト用エンジンに対してテーブルを作成(本番では lifespan がやる仕事の代行)
Base.metadata.create_all(bind=test_engine)


def override_get_db():
    """本物の get_db と完全に同じ形(yield で Session を貸す関数)。中身のDBだけが違う。

    差し替えが成立するのは『同じ呼び出し規約』を守っているから。
    インターフェースを揃えて実装だけ替える ―― DIの基本作法そのもの。
    """
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ━ DIの差し替え本体(その1: DB)━
app.dependency_overrides[get_db] = override_get_db


# ---- 偽Keycloak: 自作のRSA鍵ペアと偽JWKSクライアント ----
# 鍵生成はモジュール読み込み時に1回だけ(時間がかかる処理なので、
# テスト関数ごとに作り直さない)
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
# 「正規ではない別の鍵」。偽造トークンが弾かれることのテスト用
_ATTACKER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeSigningKey:
    """PyJWKClient が返すオブジェクトの代役。本物も .key に鍵を持つ入れ物にすぎない。"""
    def __init__(self, key):
        self.key = key


class _FakeJWKSClient:
    """本物の PyJWKClient は Keycloak へHTTPアクセスして kid に合う公開鍵を探す。
    偽物は無条件で自作の公開鍵を返す(= テストがKeycloakのフリをする)。"""
    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(_PUBLIC_KEY)


# ━ 差し替え本体(その2: Keycloak)━
# get_current_user は呼び出しのたびにモジュール変数 jwks_client を参照するので、
# ここで属性ごと挿げ替えれば、以降の全リクエストで偽物が使われる
auth_module.jwks_client = _FakeJWKSClient()

# TestClient: アプリをネットワーク無しで直接叩けるHTTPクライアント。
# with TestClient(app) as c: の形にすると lifespan(= 本物のengineへの
# create_all)が走ってしまうため、今回はあえて with なしで生成して回避している
client = TestClient(app)


# ---------- ヘルパー(テスト間の重複排除) ----------

def issue_token(
    username: str,
    *,
    issuer: str | None = None,
    expires_in_minutes: int = 60,
    key=None,
) -> str:
    """Keycloakが発行するアクセストークンの最小再現。

    sub は username から uuid5 で決定的に導出するので、同じ username で
    何度呼んでも「同一人物のトークン」になる(JITプロビジョニングが
    毎回ユーザーを作り直していないことを検証できる)。
    issuer / 期限 / 署名鍵 を引数で差し替えられるようにしてあるのは、
    「正規でないトークンが【ちゃんと弾かれる】こと」もテストするため。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, username)),
        "preferred_username": username,
        "iss": issuer or settings.keycloak_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=expires_in_minutes),
    }
    return jwt.encode(payload, key or _PRIVATE_KEY, algorithm="RS256")


def auth_headers(username: str) -> dict:
    """自前認証時代の login_headers の後継。ログインAPIを叩く代わりに
    トークンを自家発行する(Keycloak役はテスト自身なので)。"""
    return {"Authorization": f"Bearer {issue_token(username)}"}


SAMPLE_QUIZ = {
    "title": "膝抜きの主目的はどれか",
    "question": "古流武術でいう「膝抜き」の力学的な主目的として最も適切なものを選べ。",
    "choices": [
        "膝関節の屈曲筋力を最大化する",
        "支持脚の伸展トルクを抜き、重心を自由落下に近い形で先行移動させる",
        "足関節の底屈で地面を強く蹴る",
        "腰椎の前弯を強めて体幹を固定する",
    ],
    "answer_index": 1,
    "explanation": "蹴って進むのではなく、支えを外して落ちる力を移動の起点にする。",
}


# ---------- 認証まわり ----------

def test_request_without_token_returns_401():
    # ボディは完全に正しいが Authorization ヘッダーが無い。
    # 返るのは 422 ではなく【401】―― 依存解決(oauth2_scheme)が準備フェーズで
    # 先に例外を投げるため、本体どころかボディ検証の結果すら待たない。
    # 「シグネチャの Depends がエンドポイントの門番になる」ことの実証
    res = client.post("/quizzes", json=SAMPLE_QUIZ)
    assert res.status_code == 401


def test_me_provisions_user_and_hides_internal_fields():
    res = client.get("/auth/me", headers=auth_headers("keisuke"))
    assert res.status_code == 200, res.text
    body = res.json()
    # アプリには登録エンドポイントが存在しないのにユーザーが返ってくる
    # = 初回アクセス時のJITプロビジョニングが働いた証拠
    assert body["username"] == "keisuke"
    assert "id" in body and "created_at" in body
    # response_model=UserRead のフィルタが効いている証拠:
    # DB(ORMオブジェクト)側には keycloak_sub が存在するが、
    # レスポンスのJSONには現れない
    assert "keycloak_sub" not in body
    assert "hashed_password" not in body  # そもそもカラムごと消えている


def test_same_user_maps_to_same_db_row():
    first = client.get("/auth/me", headers=auth_headers("repeat_user")).json()
    second = client.get("/auth/me", headers=auth_headers("repeat_user")).json()
    # 2回目はINSERTされず既存行が返る(JITが「毎回作る」になっていないこと)。
    # sub が同じなら、何度来ても同一の users 行に対応づく
    assert first["id"] == second["id"]


def test_token_signed_with_wrong_key_returns_401():
    # 正規でない鍵で署名 = 偽造トークン。ペイロードの中身は完璧でも
    # 署名検証で落ちる。これが公開鍵方式の守りの本体
    token = issue_token("attacker", key=_ATTACKER_KEY)
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_expired_token_returns_401():
    token = issue_token("late_user", expires_in_minutes=-5)  # 5分前に失効済み
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_token_from_other_realm_returns_401():
    # 署名は正規の鍵(= 正規のKeycloakが発行した形)だが、発行者が別realm。
    # iss 検証が無いと「よそのrealmの正規ユーザー」がこのAPIを通れてしまう。
    # 署名検証だけでは防げない攻撃を issuer= の指定が塞いでいることの実証
    token = issue_token("outsider", issuer="http://keycloak.test/realms/other-app")
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ---------- クイズ投稿 ----------

def test_create_quiz_success():
    headers = auth_headers("author1")
    res = client.post("/quizzes", json=SAMPLE_QUIZ, headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["title"] == SAMPLE_QUIZ["title"]
    assert body["choices"] == SAMPLE_QUIZ["choices"]
    # owner はネストした UserRead として返る。
    # ボディに owner 情報など一切送っていないのに author1 になっている
    # = サーバがトークンから投稿者を決定している証拠
    assert body["owner"]["username"] == "author1"
    assert "keycloak_sub" not in body["owner"]


def test_create_quiz_answer_index_out_of_range_returns_422():
    headers = auth_headers("author2")
    bad = {**SAMPLE_QUIZ, "answer_index": 99}  # 99番の選択肢は存在しない
    res = client.post("/quizzes", json=bad, headers=headers)
    # schemas.QuizCreate の model_validator がエンドポイント関数の手前で弾く。
    # crud にも models にも一切到達していない
    assert res.status_code == 422


def test_list_quizzes():
    headers = auth_headers("author3")
    client.post("/quizzes", json=SAMPLE_QUIZ, headers=headers)
    res = client.get("/quizzes")  # 一覧は認証不要
    assert res.status_code == 200
    assert isinstance(res.json(), list)
    assert len(res.json()) >= 1
    # >= 1 にしているのは、テスト間でDBを共有しており他テストの投稿も
    # 残っているため。本格的には「1テストごとにDBを作り直す fixture 化」が
    # 次の一手(conftest.py に書く)


# ---------- コメント ----------

def test_comment_flow():
    # 登場人物2人: クイズの投稿者と、コメントする別人
    owner_h = auth_headers("quiz_owner")
    commenter_h = auth_headers("commenter")

    quiz_id = client.post("/quizzes", json=SAMPLE_QUIZ, headers=owner_h).json()["id"]

    # 未認証コメントは 401
    res = client.post(f"/quizzes/{quiz_id}/comments", json={"body": "面白い"})
    assert res.status_code == 401

    # 認証ありコメントは 201、author がネストして返る
    res = client.post(
        f"/quizzes/{quiz_id}/comments",
        json={"body": "選択肢2の表現が秀逸"},
        headers=commenter_h,
    )
    assert res.status_code == 201, res.text
    assert res.json()["author"]["username"] == "commenter"

    # コメント一覧
    res = client.get(f"/quizzes/{quiz_id}/comments")
    assert res.status_code == 200
    assert len(res.json()) == 1

    # クイズ詳細にもコメントが埋め込まれて返る(QuizReadWithComments)
    res = client.get(f"/quizzes/{quiz_id}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["owner"]["username"] == "quiz_owner"
    assert len(detail["comments"]) == 1
    assert detail["comments"][0]["body"] == "選択肢2の表現が秀逸"


def test_comment_on_missing_quiz_returns_404():
    headers = auth_headers("commenter2")
    res = client.post(
        "/quizzes/999999/comments", json={"body": "どこ宛?"}, headers=headers
    )
    assert res.status_code == 404


# ---------- 削除(認可) ----------

def test_delete_quiz_authorization():
    owner_h = auth_headers("real_owner")
    intruder_h = auth_headers("intruder")

    quiz_id = client.post("/quizzes", json=SAMPLE_QUIZ, headers=owner_h).json()["id"]

    # 他人のクイズは消せない: 認証(誰かは分かる)は通るが認可(権利)で 403。
    # 401と403の違いが、そのままテストの形になっている。
    # 認証がKeycloakへ移っても【認可はアプリの仕事のまま】である点に注目 ――
    # 「このクイズの持ち主は誰か」はアプリのDBにしか無い知識
    res = client.delete(f"/quizzes/{quiz_id}", headers=intruder_h)
    assert res.status_code == 403

    # 本人なら 204(No Content)、その後の取得は 404
    res = client.delete(f"/quizzes/{quiz_id}", headers=owner_h)
    assert res.status_code == 204
    assert client.get(f"/quizzes/{quiz_id}").status_code == 404

    # cascade="all, delete-orphan" でコメントも道連れに消えているので、
    # コメント一覧も(クイズ自体が無い扱いの)404 になる
    assert client.get(f"/quizzes/{quiz_id}/comments").status_code == 404
