import os


class Config:
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    CSV_MAX_BYTES = 5 * 1024 * 1024
    DEFAULT_PER_PAGE = int(os.getenv("UPB_DEFAULT_PER_PAGE", "20"))
    MAX_PER_PAGE = int(os.getenv("UPB_MAX_PER_PAGE", "100"))

    @staticmethod
    def database():
        driver = os.getenv("UPB_DB_DRIVER", "pgsql").lower()
        if driver not in {"pgsql", "postgres", "postgresql"}:
            raise RuntimeError("Seul PostgreSQL est pris en charge.")

        return {
            "host": os.getenv("UPB_DB_HOST", "localhost"),
            "dbname": os.getenv("UPB_DB_NAME", "upb_concours"),
            "user": os.getenv("UPB_DB_USER", "postgres"),
            "password": os.getenv("UPB_DB_PASS"),
            "port": int(os.getenv("UPB_DB_PORT", "5432")),
            "connect_timeout": int(os.getenv("UPB_DB_CONNECT_TIMEOUT", "5")),
        }
