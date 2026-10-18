"""Module."""


import os
import argparse


# ==========================================

# ==========================================


PORTS = {
    "L": {"low": "11001", "shadow": "11004"},
    "M": {"low": "11002", "shadow": "11005"},
    "H": {"low": "11003", "shadow": "11006"},
}


EXPERIMENTS = [

    {
        "id": "TC-01",
        "hw": "L",
        "type": "TPCC",
        "params": {"sf": 426, "terminals": 4260},
    },
    {
        "id": "TC-02",
        "hw": "L",
        "type": "TPCC",
        "params": {"sf": 655, "terminals": 6550},
    },
    {
        "id": "TC-03",
        "hw": "M",
        "type": "TPCC",
        "params": {"sf": 852, "terminals": 8520},
    },
    {
        "id": "TC-04",
        "hw": "M",
        "type": "TPCC",
        "params": {"sf": 1311, "terminals": 13110},
    },
    {
        "id": "TC-05",
        "hw": "H",
        "type": "TPCC",
        "params": {"sf": 1704, "terminals": 17040},
    },
    {
        "id": "TC-06",
        "hw": "H",
        "type": "TPCC",
        "params": {"sf": 2621, "terminals": 26210},
    },

    {"id": "TH-01", "hw": "L", "type": "TPCH", "params": {"sf": 42, "terminals": 4}},
    {"id": "TH-02", "hw": "L", "type": "TPCH", "params": {"sf": 64, "terminals": 8}},
    {"id": "TH-03", "hw": "M", "type": "TPCH", "params": {"sf": 83, "terminals": 4}},
    {"id": "TH-04", "hw": "M", "type": "TPCH", "params": {"sf": 128, "terminals": 16}},
    {"id": "TH-05", "hw": "H", "type": "TPCH", "params": {"sf": 166, "terminals": 8}},
    {"id": "TH-06", "hw": "H", "type": "TPCH", "params": {"sf": 256, "terminals": 16}},

    {
        "id": "CH-01",
        "hw": "L",
        "type": "CH",
        "params": {"sf": 655, "tp_term": 2048, "ap_term": 64},
    },
    {
        "id": "CH-02",
        "hw": "L",
        "type": "CH",
        "params": {"sf": 655, "tp_term": 4096, "ap_term": 128},
    },
    {
        "id": "CH-03",
        "hw": "M",
        "type": "CH",
        "params": {"sf": 1311, "tp_term": 4096, "ap_term": 128},
    },
    {
        "id": "CH-04",
        "hw": "M",
        "type": "CH",
        "params": {"sf": 1311, "tp_term": 8192, "ap_term": 256},
    },
    {
        "id": "CH-05",
        "hw": "H",
        "type": "CH",
        "params": {"sf": 2621, "tp_term": 8192, "ap_term": 256},
    },
    {
        "id": "CH-06",
        "hw": "H",
        "type": "CH",
        "params": {"sf": 2621, "tp_term": 16384, "ap_term": 512},
    },
]

GLOBAL_SEED = 42
# ==========================================

# ==========================================


def get_db_name(exp_id):
    """TC-01 -> tc1"""
    prefix, num = exp_id.split("-")
    return f"{prefix.lower()}{int(num)}"


def build_connection_header(port, db_name, loader_threads, db_engine):
    if db_engine == "mysql":
        return f"""<?xml version="1.0"?>
<parameters>
    <type>MYSQL</type>
    <driver>com.mysql.cj.jdbc.Driver</driver>
    <url>jdbc:mysql://localhost:{port}/{db_name}?useSSL=false&amp;rewriteBatchedStatements=true&amp;allowPublicKeyRetrieval=true</url>
    <username>root</username>
    <password>root</password>
    <reconnectOnConnectionFailure>true</reconnectOnConnectionFailure>
    <isolation>TRANSACTION_READ_COMMITTED</isolation>
    <batchsize>4096</batchsize>
    <loaderThreads>{loader_threads}</loaderThreads>
    <randomSeed>{GLOBAL_SEED}</randomSeed>
"""

    return f"""<?xml version="1.0"?>
<parameters>
    <type>POSTGRES</type>
    <driver>org.postgresql.Driver</driver>
    <url>jdbc:postgresql://localhost:{port}/{db_name}?sslmode=disable&amp;ApplicationName=chbenchmark&amp;reWriteBatchedInserts=true</url>
    <username>gaussdb</username>
    <password>CHANGE_ME</password>
    <reconnectOnConnectionFailure>true</reconnectOnConnectionFailure>
    <isolation>TRANSACTION_READ_COMMITTED</isolation>
    <batchsize>4096</batchsize>
    <loaderThreads>{loader_threads}</loaderThreads>
    <randomSeed>{GLOBAL_SEED}</randomSeed>
"""


def generate_xml_content(exp, mode, is_loader=False, db_engine="pg"):
    exp_id = exp["id"]
    hw = exp["hw"]
    exp_type = exp["type"]
    params = exp["params"]

    port = PORTS[hw][mode]
    db_name = get_db_name(exp_id)


    loader_threads = 140 if hw == "H" else 78


    # low -> 600s, shadow -> 1200s
    exec_time = 1200 if mode == "shadow" else 600

    common_header = build_connection_header(
        port=port, db_name=db_name, loader_threads=loader_threads, db_engine=db_engine
    )


    if is_loader and exp_type == "CH":

        content = f"""
    <scalefactor>{params['sf']}</scalefactor>
    <terminals>1</terminals>
    <works>
        <work>
            <time>600</time>
            <rate>10000</rate>
            <weights>45,43,4,4,4</weights>
        </work>
    </works>
    <transactiontypes>
        <transactiontype><name>NewOrder</name></transactiontype>
        <transactiontype><name>Payment</name></transactiontype>
        <transactiontype><name>OrderStatus</name></transactiontype>
        <transactiontype><name>Delivery</name></transactiontype>
        <transactiontype><name>StockLevel</name></transactiontype>
    </transactiontypes>
"""
        return common_header + content + "</parameters>"


    if exp_type == "TPCC":
        content = f"""
    <scalefactor>{params['sf']}</scalefactor>
    <terminals>{params['terminals']}</terminals>
    <works>
        <work>
            <time>{exec_time}</time>
            <rate>unlimited</rate>
            <weights>45,43,4,4,4</weights>
        </work>
    </works>
    <transactiontypes>
        <transactiontype><name>NewOrder</name></transactiontype>
        <transactiontype><name>Payment</name></transactiontype>
        <transactiontype><name>OrderStatus</name></transactiontype>
        <transactiontype><name>Delivery</name></transactiontype>
        <transactiontype><name>StockLevel</name></transactiontype>
    </transactiontypes>
"""

    elif exp_type == "TPCH":


        num_terminals = params["terminals"]
        target_rounds = 10
        # total_works = num_terminals * target_rounds
        total_works = 1

        one_work_block = """
        <work>
            <serial>true</serial>
            <rate>unlimited</rate>
            <weights>1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1</weights>
        </work>"""

        content = f"""
    <scalefactor>{params['sf']}</scalefactor>
    <terminals>{num_terminals}</terminals>
    <works>
        {one_work_block * total_works}
    </works>
    <transactiontypes>
        {''.join([f'<transactiontype><name>Q{i}</name><id>{i}</id></transactiontype>' for i in range(1, 23)])}
    </transactiontypes>
"""

    elif exp_type == "CH":
        total_terminals = params["tp_term"] + params["ap_term"]
        content = f"""
    <scalefactor>{params['sf']}</scalefactor>
    <terminals>{total_terminals}</terminals>

    <terminals bench="tpcc">{params['tp_term']}</terminals>
    <terminals bench="chbenchmark">{params['ap_term']}</terminals>

    <works>
        <work>
            <time>{exec_time}</time>
            <rate>unlimited</rate>
            <weights bench="tpcc">45,43,4,4,4</weights>
            <weights bench="chbenchmark">3, 2, 3, 2, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5</weights>
        </work>
    </works>

    <transactiontypes bench="tpcc">
        <transactiontype><name>NewOrder</name></transactiontype>
        <transactiontype><name>Payment</name></transactiontype>
        <transactiontype><name>OrderStatus</name></transactiontype>
        <transactiontype><name>Delivery</name></transactiontype>
        <transactiontype><name>StockLevel</name></transactiontype>
    </transactiontypes>

    <transactiontypes bench="chbenchmark">
        {''.join([f'<transactiontype><name>Q{i}</name></transactiontype>' for i in range(1, 23)])}
    </transactiontypes>
"""
    return common_header + content + "</parameters>"


# ==========================================

# ==========================================


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-engine", default="pg", choices=["pg", "mysql"])
    args = parser.parse_args()

    BASE_DIR = "/home/anonymous/program/resource_adaptive/shell/shell/experiment_docker_conf_and_data/conf"
    modes = ["low", "shadow"]

    print(f"Configuration...")


    if not os.path.exists(BASE_DIR):
        print(f": {BASE_DIR}")
        os.makedirs(BASE_DIR)

    for mode in modes:
        for exp in EXPERIMENTS:
            case_name = get_db_name(exp["id"])


            exec_filename = f"{BASE_DIR}/{mode}_{case_name}.xml"
            exec_content = generate_xml_content(
                exp, mode, is_loader=False, db_engine=args.db_engine
            )
            with open(exec_filename, "w", encoding="utf-8") as f:
                f.write(exec_content)
            print(f"Generated Execute Config: {exec_filename}")


            if exp["type"] == "CH":
                load_filename = f"{BASE_DIR}/{mode}_c{case_name}.xml"
                load_content = generate_xml_content(
                    exp, mode, is_loader=True, db_engine=args.db_engine
                )
                with open(load_filename, "w", encoding="utf-8") as f:
                    f.write(load_content)
                print(f"Generated Loader Config:  {load_filename}")


if __name__ == "__main__":
    main()
