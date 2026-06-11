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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import models, schemas


# ---------- User ----------

def get_user_by_sub(db: Session, keycloak_sub: str) -> models.User | None:
    # Keycloak の sub(不変ID)で1件引く。unique 制約があるので最大1件しか存在しない
    return db.scalars(
        select(models.User).where(models.User.keycloak_sub == keycloak_sub)
    ).first()


def get_or_create_user(db: Session, *, keycloak_sub: str, username: str) -> models.User:
    """JITプロビジョニング: 検証済みトークンのユーザーのDB行を「無ければ作る」。

    ユーザー登録はKeycloakの仕事になったので、アプリ側の users 行は
    「最初にAPIへアクセスされた瞬間」に自動作成する。
    毎リクエスト get_current_user から呼ばれるが、基本はSELECT1回で済み、
    INSERTが走るのはそのユーザーの初回だけ。

    引数リストの * は「これ以降はキーワード引数でしか渡せない」の意。
    get_or_create_user(db, sub値, name値) のような位置渡しを禁止することで、
    同じ str 型の2引数を取り違えるバグを呼び出し時点で防ぐ。
    """
    user = get_user_by_sub(db, keycloak_sub)

    if user is None:
        user = models.User(keycloak_sub=keycloak_sub, username=username)
        db.add(user)      # セッションに登録(まだSQLは飛ばない。INSERT予約のような状態)
        try:
            db.commit()   # ここで INSERT が flush され、トランザクションが確定する
        except IntegrityError:
            # 同じ人の「初回リクエスト」が2本並行で走ると、両方が「行が無い」と
            # 判断して INSERT し、遅い方が keycloak_sub の unique 制約で弾かれて
            # ここに来る。巻き戻して、先に勝った方の行を読み直せば正常続行できる。
            # 存在チェックとINSERTの間の競合は、最後はDBの制約で守る(従来の
            # username 重複対策と同じ考え方の、JIT版)
            db.rollback()
            user = get_user_by_sub(db, keycloak_sub)
        else:
            db.refresh(user)  # DB側で採番された id や server_default の created_at を
                              # SELECT し直してオブジェクトへ反映する。
                              # これをしないと user.id が手元で使えない
        return user

    if user.username != username:
        # Keycloak側でユーザー名が変更された場合、表示用の写しを追従させる。
        # 紐付けキーが sub だからこそ、改名しても同一ユーザーとして扱える
        user.username = username
        db.commit()
        db.refresh(user)
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
