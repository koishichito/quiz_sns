"""認証系エンドポイント: ユーザー登録とログイン。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- @router.post("/register", response_model=..., status_code=...)
    デコレータ。@f を関数定義の直上に書くと、直下の関数 g が f(g) の結果に
    置き換わる糖衣構文。ここでは「この関数を POST /auth/register の担当
    としてルーターの対応表に登録する」作業をしている。
    - response_model: 戻り値をこのスキーマで検証・フィルタしてからJSON化する
    - status_code: 成功時のHTTPステータス(省略時は200)

- 関数の引数並びがそのまま「FastAPIへの指示書」になる(仕分け規則):
    Pydanticモデル型 → JSONボディから / Depends(...) → 依存性注入。
    完全版の規則一覧は routers/quizzes.py の冒頭を参照。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import crud, schemas
# ↑ .. は「1つ上のパッケージ」(routers/ から見て app/)。相対インポートの親参照
from ..auth import create_access_token, verify_password
from ..database import get_db

# APIRouter = エンドポイントを束ねる部品。main.py の include_router で本体へ合流する。
# prefix="/auth" がこのファイル内の全パスの頭に付く(下の "/register" は /auth/register になる)。
# tags は Swagger UI 上のグルーピング表示用
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=schemas.UserRead, status_code=status.HTTP_201_CREATED
    # 201 Created = 「資源の新規作成に成功」。POSTで何かを作ったら200より201が適切。
    # status.HTTP_201_CREATED は単なる整数 201 の定数(マジックナンバー回避と自己文書化)
)
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    # この関数が呼ばれた時点で payload は検証済み(username 3〜50字、
    # password 8〜72字)であることが保証されている。不正なら FastAPI が
    # 手前で 422 を返していて、ここには到達しない
    if crud.get_user_by_username(db, payload.username) is not None:
        # 409 Conflict = 「リクエスト自体は正しいが、資源の現在状態と衝突する」。
        # 重複登録の定番コード。
        # ※ 厳密にはこの存在チェック〜INSERT の間に同名登録が割り込む競合の
        #   余地があるが、DB の unique 制約が最後の砦として弾く(その場合は
        #   500 になる。IntegrityError を捕まえて 409 に変換するのが次の改良)
        raise HTTPException(status.HTTP_409_CONFLICT, "このユーザー名は既に使われています")
    # 戻り値は hashed_password 入りの ORM User オブジェクトだが、
    # response_model=UserRead がフィルタするので外には漏れない。
    # ORMオブジェクトをそのまま return できるのは UserRead の
    # from_attributes=True のおかげ(schemas.py 参照)
    return crud.create_user(db, payload)


@router.post("/login", response_model=schemas.Token)
def login(
    # ━ クラスを依存関係として使う書き方 ━
    # Depends() と引数を空にすると「型注釈に書いたクラス自身を依存関係として
    # 呼ぶ」の意味になる(Depends(OAuth2PasswordRequestForm) と等価な省略記法)。
    #
    # OAuth2PasswordRequestForm は username / password 等を
    # 【フォーム形式(application/x-www-form-urlencoded)】から読むクラス。
    # JSONでは【ない】点が最重要:
    #   curl なら            -d "username=...&password=..."
    #   httpx/TestClient なら json= ではなく data= で送る
    # JSONで送ると 422 が返り、初学者がほぼ確実に一度ハマる。
    # この形式なのは OAuth2 のパスワードフロー仕様(RFC 6749)に合わせている
    # ため。副産物として Swagger UI の Authorize ボタンがこの形式で送ってくれる
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = crud.get_user_by_username(db, form.username)
    # 「ユーザー不在」でも「パスワード不一致」でも、同じ401・同じ文言を返す。
    # 「ユーザー名は合っている(がパスワードが違う)」と分かるレスポンスは、
    # 攻撃者に登録済みユーザー名の総当たり調査を許す(ユーザー列挙攻撃)。
    # なお Python の or は短絡評価: user が None なら verify_password は
    # 実行されない(None.hashed_password で落ちない理由)
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "ユーザー名またはパスワードが正しくありません",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Pydanticモデルを直接 return する形。token_type はデフォルトの "bearer" が入る
    return schemas.Token(access_token=create_access_token(user.id))
