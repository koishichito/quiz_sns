"""Pydanticスキーマ = APIの入出力の「契約」。

━━ 役割分担 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- ◯◯Create … クライアント→サーバの入力。【検証装置】。
              不正な入力をエンドポイント関数に到達させない盾。
- ◯◯Read   … サーバ→クライアントの出力。【フィルタ装置】。
              response_model に指定すると「ここに書いたフィールドしか
              外に出ない」ため、出してはいけない値が構造的に漏れない。

━━ なぜ ORMモデル(models.py)と分けるか ━━━━━━━━━━━━━━━━
DBの都合とAPIの都合は別物だから。
例1: users テーブルには hashed_password があるが、UserRead に書かない限り
     レスポンスには絶対含まれない(「気をつける」ではなく「漏れる経路が無い」)。
例2: QuizCreate に owner_id は無い(投稿者はトークンから決める。クライアントに
     自己申告させたら他人になりすませる)が、quizzes テーブルには owner_id がある。
     入力の形と保存の形は一致しない、が普通。
※ SQLModel はこの2層を1クラスへ融合させたライブラリ。便利な反面この境界が
  見えにくくなるため、本プロジェクトはあえて分離型(SQLAlchemy + Pydantic)で
  書いている。実務のコードベースもこの分離型が多数派。

━━ Pydantic の基本動作 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BaseModel を継承したクラスは、インスタンス化の瞬間に
  (1) 型変換を試みる("3" → 3 など、安全な範囲のみ)
  (2) 制約(Field や validator)を検証する
  (3) 失敗したら ValidationError を投げる
FastAPI はリクエストボディをこのクラスへ流し込み、ValidationError を
422 Unprocessable Entity レスポンスへ自動翻訳する。つまり
「エンドポイント関数が呼ばれた時点で payload は検証済み」が常に保証される。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━
- username: str = Field(min_length=3, max_length=50)
    フィールド宣言。: str が型、Field(...) が追加制約。
    制約が不要なら = Field(...) ごと省略してよい(id: int のように)。
- ge / le は greater-or-equal / less-or-equal(>= / <=)の略。
- model_config = ConfigDict(...) はクラス全体の設定
  (Pydantic v1 の class Config: の後継)。
- @model_validator(mode="after") は「全フィールドの個別検証が終わった後に
  呼ばれるメソッド」を登録するデコレータ。フィールド間の整合性検証に使う。
- class QuizReadWithComments(QuizRead): は継承。
  親のフィールド全部+追加分、という差分定義ができる。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------- User ----------

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    # bcrypt はアルゴリズムの仕様上、72バイトを超える部分を【黙って無視】する。
    # 「100文字のパスワードのつもりが先頭72バイトしか効いていなかった」
    # という事故を防ぐため、入口で上限を明示しておく
    password: str = Field(min_length=8, max_length=72)


class UserRead(BaseModel):
    # from_attributes=True:
    # Pydantic は通常 dict からしか値を読まないが、これを有効にすると
    # 「オブジェクトの属性」(obj.id, obj.username, ...)からも読めるようになる。
    # ORM の User インスタンスをエンドポイントからそのまま return しても
    # レスポンスに変換できるのは、この設定のおかげ。
    # (Pydantic v1 では orm_mode = True と呼ばれていた。古い記事の読み替えポイント)
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime  # datetime は ISO 8601 文字列としてJSON化される
    # hashed_password をここに書かない = 何があっても外に出ない


# ---------- 認証トークン ----------

class Token(BaseModel):
    """ログイン成功時のレスポンス。フィールド名は OAuth2 の慣例に合わせてある。"""
    access_token: str
    token_type: str = "bearer"  # デフォルト値あり = 作る側は省略できる


# ---------- Quiz ----------

class QuizCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1)
    # min_length / max_length は対象の型で意味が変わる:
    # 文字列に付ければ「文字数」、リストに付ければ「要素数」の制約になる
    choices: list[str] = Field(min_length=2, max_length=10)  # 選択肢は2〜10個
    answer_index: int = Field(ge=0)  # 0以上(負のインデックスを拒否)
    explanation: str | None = None   # 省略可能。無ければ None が入る

    @model_validator(mode="after")
    def validate_answer_index(self) -> "QuizCreate":
        """フィールド単体ではなく「フィールド間の整合性」を検証する例。

        Field(ge=0) のような単項制約では『answer_index が choices の
        範囲内か』を表現できない(2つのフィールドを見比べる必要がある)。
        mode="after" = 各フィールドの検証・型変換が全部終わった後に呼ばれる。
        だからこの時点で self.choices は検証済みの list[str] であることが
        保証されている。ValueError を raise すると Pydantic が
        422 レスポンスの詳細メッセージへ変換してくれる。
        最後に self を return するのが after バリデータの作法。
        """
        if self.answer_index >= len(self.choices):
            raise ValueError("answer_index が choices の範囲外です")
        return self


class QuizRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    question: str
    choices: list[str]
    answer_index: int
    explanation: str | None
    created_at: datetime
    # ネストしたスキーマ。ORM の quiz.owner(User オブジェクト)に対して
    # UserRead の変換が再帰的に適用される。
    # 注意: 直列化でここを読む瞬間に owner が未ロードだと遅延SELECTが走る。
    # crud.py 側で selectinload による先読みをしているのはこのため
    owner: UserRead


# ---------- Comment ----------

class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
    # quiz_id はURLから、author_id はトークンから決まるので、ボディには含めない。
    # 「クライアントに決めさせてよい情報か?」が、入力スキーマに
    # フィールドを置くか否かの判断基準


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    created_at: datetime
    author: UserRead


class QuizReadWithComments(QuizRead):
    """詳細画面用: 一覧では返さないコメント一覧を【継承】で追加したスキーマ。

    一覧APIで全クイズの全コメントまで返すと重いので、
    一覧 = QuizRead / 詳細 = QuizReadWithComments と出し分けている。
    『同じ資源でも用途ごとにレスポンスの形を変える』のは
    Read側スキーマの典型的な使い方。
    """
    comments: list[CommentRead]
