"""クイズ本体のエンドポイント: 一覧・投稿・詳細・削除。

━━ 引数の仕分け規則(このファイルが一番の見本)━━━━━━━━━━━━
FastAPI はエンドポイント関数のシグネチャ(引数の並びと型注釈)を読んで、
各引数を【どこから】調達するかを決める。規則は4つ:

  1. パスに {名前} がある           → パスパラメータ   (get_quiz の quiz_id)
  2. Pydantic の BaseModel 型       → JSONボディ       (create_quiz の payload)
  3. それ以外の単純型(int, str…)  → クエリパラメータ (list_quizzes の skip, limit)
  4. Depends(...)                   → 依存関係を実行して結果を注入

(Form / Header / Cookie だけは = Form(...) 等の明示指定が必要)
君が書くのは引数の宣言だけで、HTTPからの取り出し・型変換・検証・注入は
すべてFastAPIが準備フェーズでやる。「シグネチャ = FastAPIへの指示書」。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


# パスが ""(空文字)なのは、prefix="/quizzes" と合成されて GET /quizzes になるため
@router.get("", response_model=list[schemas.QuizRead])
def list_quizzes(
    # パスに無い単純型 → クエリパラメータ。URLでは /quizzes?skip=0&limit=20 の形。
    # Query(デフォルト値, 制約...) で範囲制約を宣言できる:
    # ?limit=9999 のような値は 422 で弾かれる(DBを守るのも入力検証の仕事)
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    # 認証不要(誰でも一覧を見られる)という設計判断。
    # response_model=list[QuizRead]: リストの各要素にスキーマ変換が適用される
    return crud.list_quizzes(db, skip=skip, limit=limit)


@router.post("", response_model=schemas.QuizRead, status_code=status.HTTP_201_CREATED)
def create_quiz(
    payload: schemas.QuizCreate,                            # 規則2: Pydanticモデル型 → JSONボディ
    db: Session = Depends(get_db),                          # 規則4: 注入(DBセッション)
    current_user: models.User = Depends(get_current_user),  # 規則4: 注入(認証済みユーザー)
    # ↑ この1行を足すだけでエンドポイントが「認証必須」になる。
    #   トークンが無い/不正なら get_current_user(の中の oauth2_scheme)が
    #   401 を投げ、本体は一度も実行されない。
    #   依存解決はボディ検証と同じ「準備フェーズ」で行われるため、
    #   『正しいボディ+認証なし』は 422 ではなく 401 になる(テストで実証済み)
):
    # 投稿者IDはクライアントの自己申告(ボディ)ではなく、トークン由来の
    # current_user.id を使う。ここを自己申告にすると誰でも他人に
    # なりすませる ―― なりすまし防止の要
    return crud.create_quiz(db, payload, owner_id=current_user.id)


@router.get("/{quiz_id}", response_model=schemas.QuizReadWithComments)
def get_quiz(quiz_id: int, db: Session = Depends(get_db)):
    # 規則1: パスの {quiz_id} と引数名 quiz_id の一致でパスパラメータとして対応付く。
    # : int 注釈により /quizzes/abc は 422、/quizzes/3 は int の 3 に変換されて届く
    quiz = crud.get_quiz_with_details(db, quiz_id)
    if quiz is None:
        # 「無い」ことの表現は None の return ではなく 404 例外。
        # HTTPException を raise した瞬間に処理は打ち切られ、
        # そのステータス/詳細でレスポンスが確定する
        raise HTTPException(status.HTTP_404_NOT_FOUND, "クイズが見つかりません")
    return quiz


# 204 No Content = 「成功したが返す中身は無い」。DELETE の定番ステータス。
# 204 を指定すると FastAPI はレスポンスボディを送らない(関数も何も return しない)
@router.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quiz(
    quiz_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    quiz = crud.get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "クイズが見つかりません")
    if quiz.owner_id != current_user.id:
        # ━ 認証と認可は別物 ━
        # 認証(authentication)= あなたが誰かは確認できた  → 失敗は 401
        # 認可(authorization) = その操作をする権利があるか → 失敗は 403
        # ここは「誰かは分かったが、他人のクイズは消せない」なので 403 Forbidden
        raise HTTPException(status.HTTP_403_FORBIDDEN, "自分のクイズしか削除できません")
    crud.delete_quiz(db, quiz)
    # return 文が無い関数は None を返す = ボディ無しの 204 と整合
