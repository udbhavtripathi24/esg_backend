"""Run RBAC seed against the configured database: python scripts_seed_rbac.py"""
from sqlmodel import Session
from app.db.session import engine
from app.rbac.seed import seed_rbac

if __name__ == "__main__":
    with Session(engine) as s:
        result = seed_rbac(s)
        print("Seeded:", result)
