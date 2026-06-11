"""SQLAlchemyのORMモデル = データベースのテーブル定義。

「DBにどう保存するか」だけに責任を持つ層。
APIの入出力の形(schemas.py)とは意図的に分離している。
クラス1個 = テーブル1個、属性1個 = カラム1個(relationship を除く)。

━━ テーブル構造の全体像 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    users ──< quizzes ──< comments
      └─────────────────<┘
    (──< は1対多。ユーザーは複数のクイズ/コメントを持ち、
     クイズは複数のコメントを持つ)

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- id: Mapped[int] = mapped_column(...)
    SQLAlchemy 2.0 の「型付き宣言スタイル」。
    Mapped[int] という型注釈そのものが『この属性はDBカラムに対応し、
    Python側の型は int』という宣言で、SQLAlchemy はクラス定義時に
    この注釈を実行時に読み取る。X[Y] はジェネリクス記法(型パラメータ)で、
    list[str] と同じ仕組み。
    右辺の mapped_column(...) はカラムの詳細(主キー・制約・デフォルト等)。
    詳細が不要なら右辺ごと省略できる(answer_index がその例)。

- Mapped[str | None]
    str | None は「str または None」(Python 3.10+ の Union 記法。
    旧 Optional[str] と同義)。SQLAlchemy はここから NULL 許可
    (nullable=True)を自動推論する。逆に Mapped[str] なら NOT NULL。
    型注釈が制約を兼ねている。

- Mapped[list["Quiz"]] の "Quiz"(文字列)
    前方参照。User クラスを定義している時点では Quiz クラスがまだ
    下に書かれていない(未定義)ので、名前を文字列にして評価を遅らせる。

- relationship(back_populates="...")
    これはカラムでは【ない】(CREATE TABLE に現れない)。外部キーを
    足がかりに「関連オブジェクトへ Python の属性としてアクセスする」ための
    ORM機能。quiz.owner と書くと(未ロードなら)裏で SELECT が走り User が返る。
    back_populates は双方向リンクの宣言: user.quizzes と quiz.owner が
    互いに同期するよう、相手側の属性名を指定し合う。

- server_default=func.now()
    デフォルト値を【DBサーバ側】で入れる(DDL に DEFAULT CURRENT_TIMESTAMP
    が付く)。Python側 default=datetime.now との違い: アプリを経由しない
    INSERT でも値が入る/時刻の基準がDBサーバ1箇所に統一される。

- ForeignKey("users.id", ondelete="CASCADE")
    参照先は『テーブル名.カラム名』の文字列で書く(クラス名 User では
    ない点に注意 ―― これはDDLレベルの話なので、DBの世界の名前を使う)。
    ondelete="CASCADE" は【DB側】の外部キー制約として
    「親行が消えたら子行も消す」を宣言する。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    # クラス名(User)は Python 側の名前、__tablename__ は DB 側のテーブル名。
    # 「クラスは単数形・テーブルは複数形」が広く使われる慣例
    __tablename__ = "users"

    # primary_key=True: 主キー。autoincrement=True: 採番をDBに任せる
    # (MySQLのAUTO_INCREMENT)。よって INSERT 時に id は渡さず、
    # commit 後に refresh で読み戻す(crud.py 参照)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Keycloak が発行する不変のユーザーID(トークンの sub クレーム。UUID形式36文字)。
    # アプリ内の主キー id とは別物で、「Keycloak上の誰か」と「アプリDBの行」を
    # つなぐ外部連携キー。毎リクエストこの値で検索するので index 必須。
    # unique=True は同じ人の行が2つできないための最後の砦
    # (JITプロビジョニングの並行実行対策。crud.get_or_create_user 参照)。
    # MySQL の VARCHAR は長さ指定が必須。素の String だと create_all の
    # DDL生成時に落ちる(SQLite は許すので、SQLiteだけで開発していると
    # MySQLに繋いだ瞬間に発覚する罠)
    keycloak_sub: Mapped[str] = mapped_column(String(36), unique=True, index=True)

    # 表示用ユーザー名。Keycloak の preferred_username の【写し(キャッシュ)】。
    # hashed_password カラムは消えた ―― パスワードを預かるのはKeycloakの仕事に
    # なったので、漏洩リスクごとアプリから消滅した。
    # 自前認証時代は unique=True だったが、ユーザー名の一意性を管理する責任も
    # Keycloak 側へ移ったので、アプリ側では一意制約を持たない
    # (真実が2箇所にあると必ず食い違う ―― Single Source of Truth の原則)
    username: Mapped[str] = mapped_column(String(255), index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # DB側で CURRENT_TIMESTAMP を入れる
    )

    # ---- ここから下はカラムではなく relationship(Python側のつながり)----
    # user.quizzes で「このユーザーが作ったクイズのリスト」が取れる。
    # Quiz.owner 側と back_populates で双方向に結ばれている
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="owner")
    comments: Mapped[list["Comment"]] = relationship(back_populates="author")


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200))
    # Text は長さ無制限の文字列型(MySQLではTEXT)。VARCHARと違い長さ指定不要
    question: Mapped[str] = mapped_column(Text)

    # 選択肢のリストを MySQL の JSON 型カラム(5.7+)へ丸ごと格納する。
    # 例: ["足首", "膝", "股関節", "肩"]
    # Python の list <-> DB の JSON の相互変換は SQLAlchemy が自動でやる。
    # 別テーブル(choices)に正規化する設計もあり得るが、
    # 「選択肢単体で検索しない」「常にクイズと一緒に読む」ので
    # JSONで十分 ―― というトレードオフ判断
    choices: Mapped[list[str]] = mapped_column(JSON)

    # mapped_column(...) を省略した最小形。
    # Mapped[int] だけで「INT NOT NULL のカラム」として扱われる
    answer_index: Mapped[int]  # 正解のインデックス(0始まり)

    # str | None なので nullable=True が自動推論される(解説は任意項目)
    explanation: Mapped[str | None] = mapped_column(Text)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 多対1側(クイズから見て投稿者は1人)なので list ではなく単数
    owner: Mapped["User"] = relationship(back_populates="quizzes")

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="quiz",
        # ━ ORM側のカスケード ━
        # db.delete(quiz) したとき、ぶら下がる Comment もセッション上で
        # 一緒に削除対象にする。delete-orphan は「親から切り離された
        # コメントも削除」。DB側の ondelete="CASCADE" と二重化して
        # あるのは、ORM経由でもSQL直叩きでも整合が保たれるようにするため
        cascade="all, delete-orphan",
        order_by="Comment.created_at",  # quiz.comments を読むときの並び順
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    quiz: Mapped["Quiz"] = relationship(back_populates="comments")
    author: Mapped["User"] = relationship(back_populates="comments")
