from app.config import get_settings
from app.db.session import build_admin_engine
from demo.seed.generate import seed_database


def main() -> None:
    counts = seed_database(build_admin_engine(get_settings()))
    print(f"Seed version: commerce-v1; counts: {counts}")


if __name__ == "__main__":
    main()
