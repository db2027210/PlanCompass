import psycopg2
import pymysql
from dbutils.pooled_db import PooledDB
from typing import List, Tuple, Any

# ==========================================

# ==========================================
ACTIVE_POOL = None
DB_TYPE = None


def init_db_pool(
    db_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    max_workers: int = 50,
):
    """init db pool."""


    global ACTIVE_POOL, DB_TYPE
    DB_TYPE = db_type.lower()

    if DB_TYPE in ["pg", "postgresql", "opengauss"]:
        ACTIVE_POOL = PooledDB(
            creator=psycopg2,
            maxconnections=max_workers * 2,
            mincached=10,
            maxcached=50,
            blocking=True,
            ping=0,
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
        )
    elif DB_TYPE == "mysql":
        ACTIVE_POOL = PooledDB(
            creator=pymysql,
            maxconnections=max_workers * 2,
            mincached=10,
            maxcached=50,
            blocking=True,
            ping=0,
            host=host,
            port=port,
            user=user,
            password=password,
            database=dbname,
            charset="utf8mb4",
        )
    else:
        raise ValueError(f": {db_type}")


# ==========================================

# ==========================================


def execute_sql(sql: str, params: Any = "") -> List[Tuple[Any, ...]]:
    """execute sql."""


    if ACTIVE_POOL is None:
        raise RuntimeError(" init_db_pool()")


    if params is None:
        params = ""
    elif not isinstance(params, str):
        params = str(params)


    sql_params = ()
    if params.strip():
        sql_params = tuple(p.strip() for p in params.split(","))


    sql = sql.replace("?", "%s")

    conn = None
    cursor = None
    results = []

    try:

        conn = ACTIVE_POOL.connection()
        cursor = conn.cursor()


        cursor.execute(sql, sql_params)


        if cursor.description is not None:
            results = cursor.fetchall()
        else:
            conn.commit()

    except Exception as e:
        print(f"{DB_TYPE.upper()} : {e}")

        if conn:
            conn.rollback()

    finally:

        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return results
