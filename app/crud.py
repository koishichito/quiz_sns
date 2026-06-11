"""DB操作(CRUD)を関数として切り出す層。エンドポイント(routers/)から呼ばれる。

━━ この層を分ける理由 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- エンドポイントは「HTTPの都合」(ステータスコード、認可判断)に集中し、
  「SQLの都合」(クエリの組み方、先読み戦略)はここへ隔離する
- 同じDB操作を複数のエンドポイントから再利用できる
- 引数で Session を受け取り、自分では作らない(ここにも依存性注入の思想。
  受け取る設計だから、テストでは別のセッションを渡すだけで差し替わる)

━━ commit の責務はこの層に置く方針で統一 ━━━━━━━━━━━━━━━━
「書き込み系の crud 関数は、戻った時点でDBに確定している」という契約。
※ これは流儀の分かれる設計判断。エンドポイント側や get_db の後半で
  まとめて commit する流儀(Unit of Work パターン)もある。
  どちらが正解というより、プロジェクト内で統一されていることが重要。

━━ このファイルで登場する記法(SQLAlchemy 2.0 スタイル)━━━━━━
- select(models.User).where(...)
    SQL の SELECT 文を表す【オブジェクト】を組み立てるだけで、まだ実行
    されない。メソッドチェーンで .where() .order_by() .offset() ... と
    条件を積む。実行されるのは db.scalars(stmt) 等へ渡した瞬間。
    (旧1.x系の db.query(User).filter(...) と同じことの現代版。
     古い記事を読むときの最大の読み替えポイント)
- db.scalars(stmt)
    実行し、各行の先頭要素(ここではエンティティそのもの)を順に返す。
    .first() で最初の1件(無ければ None)、.all() で全件。
- models.User.username == username
    Python の比較演算子に見えるが、SQLAlchemy が == を上書き
    (演算子オーバーロード)していて、bool ではなく「SQLの条件式
    オブジェクト」が返る。だから where(...) に渡せる。
- db.get(Model, 主キー)
    主キー検索の専用ショートカット。
- **data.model_dump()
    model_dump() は Pydanticモデル → dict 変換(v1 の .dict() の後継)。
    f(**d) は辞書をキーワード引数へ展開する記法:
    f(title=..., question=..., ...) と書いたのと同じになる。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models, schemas
from .auth import hash_password


# ---------- User ----------

def get_user_by_username(db: Session, username: str) -> models.User | None:
    # ユーザー名で1件引く。unique 制約があるので最大1件しか存在しない
    return db.scalars(
        select(models.User).where(models.User.username == username)
    ).first()


def create_user(db: Session, data: schemas.UserCreate) -> models.User:
    user = models.User(
        username=data.username,
        hashed_password=hash_password(data.password),  # 保存前に必ずハッシュ化
    )
    db.add(user)      # セッションに登録(まだSQLは飛ばない。INSERT予約のような状態)
    db.commit()       # ここで INSERT が flush され、トランザクションが確定する
    db.refresh(user)  # DB側で採番された id や server_default の created_at を
                      # SELECT し直してオブジェクトへ反映する。
                      # これをしないと user.id が手元で使えない
    return user


# ---------- Quiz ----------

def create_quiz(db: Session, data: schemas.QuizCreate, owner_id: int) -> models.Quiz:
    # model_dump() で {"title": ..., "choices": [...], ...} の dict を作り、
    # ** で展開して Quiz(title=..., choices=..., ...) に渡す。
    # owner_id はボディ由来ではなくトークン由来なので、別引数で明示的に合流させる
    quiz = models.Quiz(**data.model_dump(), owner_id=owner_id)
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


def list_quizzes(db: Session, skip: int = 0, limit: int = 20) -> list[models.Quiz]:
    stmt = (
        select(models.Quiz)
        # ━ N+1問題とその対策 ━
        # このあと response_model(QuizRead)が各クイズの .owner を読む。
        # 無対策だと「一覧で1回 + クイズごとに owner 取得でN回」の SELECT が
        # 走る(N+1問題)。selectinload は「一覧で取れた quiz の owner_id を
        # 集め、SELECT ... WHERE users.id IN (...) を【追加で1回だけ】発行」
        # する先読み戦略。合計2クエリで固定になる。
        # database.py の echo=True にすると実際に観察できる
        .options(selectinload(models.Quiz.owner))
        # created_at が同時刻のときに順序が不定にならないよう id を第2キーに
        .order_by(models.Quiz.created_at.desc(), models.Quiz.id.desc())
        .offset(skip)   # ページネーション: skip 件読み飛ばして
        .limit(limit)   # limit 件だけ返す
    )
    return list(db.scalars(stmt).all())


def get_quiz(db: Session, quiz_id: int) -> models.Quiz | None:
    return db.get(models.Quiz, quiz_id)  # 主キー検索の最短形


def get_quiz_with_details(db: Session, quiz_id: int) -> models.Quiz | None:
    """詳細表示用: 投稿者とコメント(と各コメントの投稿者)を先読みして取得。"""
    stmt = (
        select(models.Quiz)
        .where(models.Quiz.id == quiz_id)
        .options(
            selectinload(models.Quiz.owner),
            # selectinload は . でつないで多段にできる:
            # クイズのコメント一覧 → 各コメントの投稿者、と2段先読み。
            # 結果、クイズ+owner+comments+authors で計4クエリ程度に固定される
            selectinload(models.Quiz.comments).selectinload(models.Comment.author),
        )
    )
    return db.scalars(stmt).first()


def delete_quiz(db: Session, quiz: models.Quiz) -> None:
    db.delete(quiz)  # relationship の cascade="all, delete-orphan" 設定により、
                     # ぶら下がるコメントも一緒に削除される
    db.commit()


# ---------- Comment ----------

def create_comment(
    db: Session, quiz_id: int, author_id: int, data: schemas.CommentCreate
) -> models.Comment:
    comment = models.Comment(quiz_id=quiz_id, author_id=author_id, body=data.body)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


def list_comments(db: Session, quiz_id: int) -> list[models.Comment]:
    stmt = (
        select(models.Comment)
        .where(models.Comment.quiz_id == quiz_id)
        .options(selectinload(models.Comment.author))  # CommentRead が author を読むため先読み
        .order_by(models.Comment.created_at.asc(), models.Comment.id.asc())
    )
    return list(db.scalars(stmt).all())
