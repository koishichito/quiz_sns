"""コメント関連のエンドポイント。ネストしたURL設計の見本。

━━ URL設計の考え方 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
コメントは必ず特定のクイズに属する(単独では存在しない)従属資源なので、
    /quizzes/{quiz_id}/comments
という階層URLで所属関係をそのまま表現する(RESTの基本)。
/comments?quiz_id=3 のようなフラット設計でも動くが、
資源同士の関係がURLから読み取れなくなる。

━━ 記法ポイント ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- prefix に {quiz_id} のようなパスパラメータを含めてよい。
  prefix 内のパラメータも、各エンドポイント関数の引数として普通に受け取れる。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/quizzes/{quiz_id}/comments", tags=["comments"])


@router.get("", response_model=list[schemas.CommentRead])
def list_comments(quiz_id: int, db: Session = Depends(get_db)):
    # 存在しないクイズへの問い合わせは「空リスト」ではなく 404 にする。
    # 空リストを返す設計だと「クイズはあるがコメント0件」と
    # 「クイズ自体が無い」をクライアント側で区別できなくなる
    if crud.get_quiz(db, quiz_id) is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return crud.list_comments(db, quiz_id)


@router.post(
    "", response_model=schemas.CommentRead, status_code=status.HTTP_201_CREATED
)
def create_comment(
    # 4種類の調達元が1つの関数に勢揃いしている(仕分け規則の総集編):
    quiz_id: int,                                            # ← prefix の {quiz_id}(パス)から
    payload: schemas.CommentCreate,                          # ← JSONボディから
    db: Session = Depends(get_db),                           # ← DIで注入
    current_user: models.User = Depends(get_current_user),   # ← DIで注入(これで認証必須になる)
):
    if crud.get_quiz(db, quiz_id) is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    # quiz_id はURL由来、author_id はトークン由来、body はボディ由来。
    # 出所の異なる3つの情報がここで初めて合流する
    return crud.create_comment(
        db, quiz_id=quiz_id, author_id=current_user.id, data=payload
    )
