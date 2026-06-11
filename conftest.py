"""pytest が自動で読み込む特別なファイル。中身が空に近くても存在自体に意味がある。

pytest はテスト収集時、プロジェクトルートにあるこのファイルを見つけると
このディレクトリを import の検索パス(sys.path)に加える。そのおかげで
tests/ の中から `from app.main import app` という import が通る。
(これが無いと ModuleNotFoundError: No module named 'app' になりがち)

本来は複数テストで共有する fixture を置く場所でもある(今回は未使用)。
"""
