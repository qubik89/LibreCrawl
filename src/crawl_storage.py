import os


def result_storage_mode():
    mode = os.getenv('CRAWL_RESULT_STORAGE', 'both').lower()
    return mode if mode in ('sqlite', 'clickhouse', 'both') else 'both'


def should_save_sqlite_rows(storage_mode, has_rows, clickhouse_rows_saved):
    return has_rows and storage_mode in ('sqlite', 'both')
