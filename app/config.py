"""アプリ全体の設定を一箇所に集める層。環境変数 / .env ファイルから読み込む。

━━ なぜこの層があるか ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DB接続文字列やKeycloakの接続先のような「環境ごとに変わる値」「秘密の値」を
ソースコードに直書きすると、
  (1) git に秘密が残る(漏洩事故の典型)
  (2) 開発/本番で値を切り替えるたびにコードを書き換える羽目になる
ため、コードの外(環境変数 or .env)へ追い出し、読む窓口をここに一本化する。
いわゆる「12 Factor App」の Config 原則。

━━ このファイルで登場する記法 ━━━━━━━━━━━━━━━━━━━━━━━━━━
- class Settings(BaseSettings):
    クラス定義。括弧内は親クラス(継承元)。BaseSettings を継承すると
    「インスタンス化した瞬間に環境変数 / .env を読みに行く」能力が手に入る。

- database_url: str = "..."
    一見ただのクラス変数だが、pydantic 系クラスではこれが【フィールド宣言】:
      フィールド名  = 読みに行く環境変数名(大文字小文字は区別しない)
      : str         = その値に要求する型(合わなければ起動時に ValidationError)
      = "..."       = 環境変数が無かったときのデフォルト値
    つまり database_url: str は「環境変数 DATABASE_URL を str として読め」の意。
    型注釈が実行時に意味を持つ ―― FastAPI/Pydantic/SQLAlchemy 共通の世界観。

- model_config = SettingsConfigDict(...)
    クラス全体の動作設定。pydantic v2 では予約名 model_config に設定を
    代入する流儀(v1 の class Config: の後継。古い記事との読み替えポイント)。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
# ↑ pydantic-settings は pydantic 本体から分離された別パッケージ。
#   「環境変数や .env を読んで pydantic モデルに流し込む」専用の道具。


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",            # カレントディレクトリの .env ファイルも読む
        env_file_encoding="utf-8",  # .env の文字コード
        extra="ignore",             # .env に未知のキーがあっても無視する。
        # ↑ これを指定しないと、関係ない変数が .env にあるだけで
        #   ValidationError で起動失敗する。共有 .env 運用では ignore が無難
    )

    # 値の優先順位: OSの環境変数 > .env ファイル > このデフォルト値
    # 形式: mysql+pymysql://ユーザー:パスワード@ホスト:ポート/DB名?charset=utf8mb4
    #       ~~~~~ ~~~~~~~
    #       DB方言  ドライバ名。SQLAlchemy はこの文字列だけで
    #               「MySQL に PyMySQL 経由で話す」ことを理解する
    database_url: str = (
        "mysql+pymysql://quiz_user:quiz_pass@127.0.0.1:3306/quiz_db?charset=utf8mb4"
    )
    # ↑ 文字列を ( ) で囲んで改行しているのは単に1行が長いから。
    #   括弧の中では自由に改行できる(Python の行継続の基本)

    # ━ Keycloak(外部の認証サーバ)関連 ━
    # 認証はアプリの外 = Keycloak に委譲した。だから SECRET_KEY(自前の署名鍵)は
    # もう存在しない。アプリが知るべきは「どの Keycloak の、どの realm を
    # 信用するか」だけ。値は配られた環境のものを .env に書く
    keycloak_server_url: str = "http://localhost:8080"
    keycloak_realm: str = "quiz-sns"

    # トークン取得時にクライアントが名乗るID。トークンの【検証】そのものには
    # 使わないが、Swagger UI のログイン導線や README の curl 例で参照する
    keycloak_client_id: str = "quiz-sns-api"

    # str | None = None なので「設定しなくてよい」項目。
    # 設定すると aud(宛先)クレームの検証が有効になる(auth.py 参照)。
    # Keycloak は既定では aud にこのAPIの名前を入れない(クライアント側の
    # マッパー設定に依存する)ため、配られた環境に合わせてオン/オフできる形にしてある
    keycloak_audience: str | None = None

    # ━ @property = 「計算で求まる設定値」━
    # 下の3つは独立した設定ではなく server_url と realm から機械的に決まる。
    # 別々の環境変数にすると「realm だけ変えて issuer を変え忘れる」事故が
    # 起きるので、導出ロジックごと一箇所に固める

    @property
    def keycloak_issuer(self) -> str:
        """トークンの iss(発行者)クレームと厳密一致すべき値。
        Keycloak では realm のURLがそのまま発行者名になる。"""
        return f"{self.keycloak_server_url.rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def keycloak_jwks_url(self) -> str:
        """署名検証用の公開鍵一覧(JWKS)の配布エンドポイント。"""
        return f"{self.keycloak_issuer}/protocol/openid-connect/certs"

    @property
    def keycloak_token_url(self) -> str:
        """トークン発行エンドポイント。ログインの宛先はアプリではなく【ここ】。"""
        return f"{self.keycloak_issuer}/protocol/openid-connect/token"


# モジュール読み込み時に1回だけインスタンス化し、全モジュールでこれを使い回す
# (シングルトンの簡易形)。import の瞬間に検証が走るので、設定ミスは
# 「最初のリクエスト時」ではなく「起動時」に発覚する。早く失敗するのは美徳
settings = Settings()
