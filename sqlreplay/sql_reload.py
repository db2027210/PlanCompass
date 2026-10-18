import argparse
import pandas as pd
import polars as pl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


from db_conn import init_db_pool, execute_sql


def parse_args():
    """parse args."""
    parser = argparse.ArgumentParser(description="Benchbase SQL ")


    parser.add_argument(
        "--db-type",
        type=str,
        required=True,
        choices=["mysql", "pg", "opengauss"],
        help=" (mysql, pg, opengauss)",
    )
    parser.add_argument("--host", type=str, required=True, help=" IP")
    parser.add_argument("--port", type=int, required=True, help="")
    parser.add_argument("--user", type=str, required=True, help="")
    parser.add_argument("--password", type=str, required=True, help="")
    parser.add_argument("--dbname", type=str, required=True, help="")


    parser.add_argument(
        "--csv-file", type=str, required=True, help=" CSV "
    )
    parser.add_argument(
        "--workers", type=int, default=50, help=" (: 50)"
    )

    return parser.parse_args()


def main():

    args = parse_args()


    print(f" {args.db_type.upper()} ...")
    init_db_pool(
        db_type=args.db_type,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        dbname=args.dbname,
        max_workers=args.workers,
    )

    print(
        f" Pandas  Polars  (: {args.csv_file})..."
    )


    pdf = pd.read_csv(args.csv_file)


    q = (
        pl.from_pandas(pdf).lazy()

        .with_columns(pl.col("StartTime_Epoch").str.to_datetime(time_zone="UTC"))

        .sort("StartTime_Epoch")
        # .with_row_index("row_num")

        # .filter(pl.col("row_num") % step == 0)
    )


    df = q.collect()

    total_queries = len(df)
    print(f" {total_queries}  SQL ")

    if total_queries == 0:
        print("")
        return


    base_timestamp = df["StartTime_Epoch"][0]

    print(f" SQL... (: {args.workers})")

    real_world_start_time = time.time()


    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for row in df.iter_rows(named=True):
            sql = row["SQL"]

            params = row["Parameters"] if row["Parameters"] is not None else ""
            target_time = row["StartTime_Epoch"]


            relative_delay_sec = (target_time - base_timestamp).total_seconds()


            elapsed_real_sec = time.time() - real_world_start_time


            sleep_time = relative_delay_sec - elapsed_real_sec
            if sleep_time > 0:
                time.sleep(sleep_time)


            executor.submit(execute_sql, sql, params)

    print(" SQL ...")


if __name__ == "__main__":
    main()
