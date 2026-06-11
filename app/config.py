"""アプリ全体の設定を一箇所に集める層。環境変数 / .env ファイルから読み込む。

━━ なぜこの層があるか ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DB接続文字列やJWT署名鍵のような「環境ごとに変わる値」「秘密の値」を
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

    # JWTの署名鍵。これが漏れるとトークンを偽造できてしまう。
    # 本番では必ず .env でランダム値に上書きする(openssl rand -hex 32 など)
    secret_key: str = "dev-only-secret-change-me"

    # int 宣言なので、環境変数の "60"(文字列)も 60(整数)に自動変換される
    access_token_expire_minutes: int = 60


# モジュール読み込み時に1回だけインスタンス化し、全モジュールでこれを使い回す
# (シングルトンの簡易形)。import の瞬間に検証が走るので、設定ミスは
# 「最初のリクエスト時」ではなく「起動時」に発覚する。早く失敗するのは美徳
settings = Settings()
