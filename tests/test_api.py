"""結合テスト: 本物のHTTPリクエストの形でAPI全体を通しで検証する。

━━ ここが依存性注入の最大の見せ場 ━━━━━━━━━━━━━━━━━━━━
app.dependency_overrides[get_db] = override_get_db の【1行】で、
アプリ本体のコードを1文字も変えずに MySQL → インメモリSQLite へ差し替わる。
「資源を自分で作らず引数で受け取る設計にしておいたから、外から付け替えられる」
―― DIの実利が最も具体的な形で現れる場所。

━━ このテストの層について ━━━━━━━━━━━━━━━━━━━━━━━━━━
関数単体のユニットテストではなく、
HTTP → ルーティング → 検証 → DI → CRUD → DB → 直列化
の全経路を通す【結合テスト】。FastAPI は TestClient のおかげで
結合テストが安く書けるので、まずここから固めるのが効率的。

実行: プロジェクトルートで  pytest tests/ -q
pytest は test_ で始まる関数を自動収集して実行し、
assert が False になったらその場で失敗として報告する。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- assert 条件, メッセージ
    条件が False なら AssertionError。第2引数(res.text 等)は
    失敗時に表示されるので、デバッグの手がかりとして仕込んでおく。
- {**SAMPLE_QUIZ, "answer_index": 99}
    dict のコピー+一部キーの上書き(マージ記法)。元の dict は変更されない。
- f"Bearer {token}"
    f文字列。{} 内の式を評価して文字列に埋め込む。
"""
import os

# ━ app パッケージを import する【前】に環境変数を立てる(この順序が重要)━
# config.py は import された瞬間に Settings() を実行して環境変数 / .env を読む。
# .env の無いCI環境でもデフォルト値で動きはするが、万一にも本物のMySQLを
# 読みに行かないよう、無害な値で明示的に塞いでおく保険。
# setdefault = 「未設定のときだけセット」(既にある設定は尊重する)
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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


# ━ DIの差し替え本体 ━
# dependency_overrides の正体はただの dict。
# 「キーの関数が Depends で要求されたら、代わりに値の関数を呼べ」という対応表で、
# アプリ中の Depends(get_db) が(get_current_user の中のものも含めて)
# 全部 override_get_db に化ける
app.dependency_overrides[get_db] = override_get_db

# TestClient: アプリをネットワーク無しで直接叩けるHTTPクライアント。
# client.post("/auth/register", json=...) と本物そっくりに使えるが、
# 実際はソケットを介さずASGI関数を直接呼んでいる(高速・ポート不要)。
# with TestClient(app) as c: の形にすると lifespan(= 本物のengineへの
# create_all)が走ってしまうため、今回はあえて with なしで生成して回避している
client = TestClient(app)


# ---------- ヘルパー(テスト間の重複排除) ----------

def register(username: str, password: str = "password123"):
    return client.post(
        "/auth/register", json={"username": username, "password": password}
    )


def login_headers(username: str, password: str = "password123") -> dict:
    """ログインして Authorization ヘッダーの形(dict)にして返す。"""
    res = client.post(
        "/auth/login",
        # OAuth2PasswordRequestForm はJSONではなくフォーム形式を要求する。
        # httpx/TestClient では json= ではなく data= で送る(json= だと422になる)
        data={"username": username, "password": password},
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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

def test_register_returns_201_and_hides_password():
    res = register("keisuke")
    assert res.status_code == 201
    body = res.json()
    assert body["username"] == "keisuke"
    assert "id" in body and "created_at" in body
    # response_model=UserRead のフィルタが効いている証拠:
    # DB(ORMオブジェクト)側には hashed_password が存在するが、
    # レスポンスのJSONには現れない
    assert "hashed_password" not in body
    assert "password" not in body


def test_register_duplicate_username_returns_409():
    register("dup_user")
    res = register("dup_user")  # 2回目は重複
    assert res.status_code == 409


def test_login_success_and_wrong_password():
    register("login_user")
    ok = client.post(
        "/auth/login", data={"username": "login_user", "password": "password123"}
    )
    assert ok.status_code == 200
    assert ok.json()["token_type"] == "bearer"

    ng = client.post(
        "/auth/login", data={"username": "login_user", "password": "wrong-password"}
    )
    assert ng.status_code == 401


# ---------- クイズ投稿 ----------

def test_create_quiz_requires_auth():
    # ボディは完全に正しいが Authorization ヘッダーが無い。
    # 返るのは 422 ではなく【401】―― 依存解決(oauth2_scheme)が準備フェーズで
    # 先に例外を投げるため、本体どころかボディ検証の結果すら待たない。
    # 「シグネチャの Depends がエンドポイントの門番になる」ことの実証
    res = client.post("/quizzes", json=SAMPLE_QUIZ)
    assert res.status_code == 401


def test_create_quiz_success():
    register("author1")
    headers = login_headers("author1")
    res = client.post("/quizzes", json=SAMPLE_QUIZ, headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["title"] == SAMPLE_QUIZ["title"]
    assert body["choices"] == SAMPLE_QUIZ["choices"]
    # owner はネストした UserRead として返る。
    # ボディに owner 情報など一切送っていないのに author1 になっている
    # = サーバがトークンから投稿者を決定している証拠
    assert body["owner"]["username"] == "author1"
    assert "hashed_password" not in body["owner"]


def test_create_quiz_answer_index_out_of_range_returns_422():
    register("author2")
    headers = login_headers("author2")
    bad = {**SAMPLE_QUIZ, "answer_index": 99}  # 99番の選択肢は存在しない
    res = client.post("/quizzes", json=bad, headers=headers)
    # schemas.QuizCreate の model_validator がエンドポイント関数の手前で弾く。
    # crud にも models にも一切到達していない
    assert res.status_code == 422


def test_list_quizzes():
    register("author3")
    headers = login_headers("author3")
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
    register("quiz_owner")
    register("commenter")
    owner_h = login_headers("quiz_owner")
    commenter_h = login_headers("commenter")

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
    register("commenter2")
    headers = login_headers("commenter2")
    res = client.post(
        "/quizzes/999999/comments", json={"body": "どこ宛?"}, headers=headers
    )
    assert res.status_code == 404


# ---------- 削除(認可) ----------

def test_delete_quiz_authorization():
    register("real_owner")
    register("intruder")
    owner_h = login_headers("real_owner")
    intruder_h = login_headers("intruder")

    quiz_id = client.post("/quizzes", json=SAMPLE_QUIZ, headers=owner_h).json()["id"]

    # 他人のクイズは消せない: 認証(誰かは分かる)は通るが認可(権利)で 403。
    # 401と403の違いが、そのままテストの形になっている
    res = client.delete(f"/quizzes/{quiz_id}", headers=intruder_h)
    assert res.status_code == 403

    # 本人なら 204(No Content)、その後の取得は 404
    res = client.delete(f"/quizzes/{quiz_id}", headers=owner_h)
    assert res.status_code == 204
    assert client.get(f"/quizzes/{quiz_id}").status_code == 404

    # cascade="all, delete-orphan" でコメントも道連れに消えているので、
    # コメント一覧も(クイズ自体が無い扱いの)404 になる
    assert client.get(f"/quizzes/{quiz_id}/comments").status_code == 404
