#!/usr/bin/env python3
"""Module."""


import argparse
import os
import sys
import xml.etree.ElementTree as ET


# 1. TPC-C Configuration (c1 - c30)
TPCC_CASES = {
    "c1": ("L", 72),
    "c2": ("L", 143),
    "c3": ("L", 215),
    "c4": ("L", 286),
    "c5": ("L", 358),
    "c6": ("L", 430),
    "c7": ("L", 501),
    "c8": ("L", 573),
    "c9": ("L", 645),
    "c10": ("L", 716),
    "c11": ("M", 143),
    "c12": ("M", 286),
    "c13": ("M", 430),
    "c14": ("M", 573),
    "c15": ("M", 716),
    "c16": ("M", 860),
    "c17": ("M", 1003),
    "c18": ("M", 1146),
    "c19": ("M", 1289),
    "c20": ("M", 1433),
    "c21": ("H", 286),
    "c22": ("H", 573),
    "c23": ("H", 860),
    "c24": ("H", 1146),
    "c25": ("H", 1433),
    "c26": ("H", 1719),
    "c27": ("H", 2006),
    "c28": ("H", 2292),
    "c29": ("H", 2579),
    "c30": ("H", 2865),
}

# 2. TPC-H Configuration (h1 - h30)
TPCH_CASES = {
    "h1": ("L", 2),
    "h2": ("L", 5),
    "h3": ("L", 7),
    "h4": ("L", 10),
    "h5": ("L", 10),
    "h6": ("L", 12),
    "h7": ("L", 15),
    "h8": ("L", 17),
    "h9": ("L", 19),
    "h10": ("L", 22),
    "h11": ("M", 5),
    "h12": ("M", 10),
    "h13": ("M", 12),
    "h14": ("M", 17),
    "h15": ("M", 22),
    "h16": ("M", 24),
    "h17": ("M", 29),
    "h18": ("M", 34),
    "h19": ("M", 39),
    "h20": ("M", 44),
    "h21": ("H", 10),
    "h22": ("H", 17),
    "h23": ("H", 24),
    "h24": ("H", 34),
    "h25": ("H", 44),
    "h26": ("H", 49),
    "h27": ("H", 58),
    "h28": ("H", 68),
    "h29": ("H", 78),
    "h30": ("H", 88),
}

# 3. CH-Benchmark Configuration (ch1 - ch60)
CH_CASES = {
    "ch1": ("L", 72, 256, 8),
    "ch2": ("L", 72, 512, 16),
    "ch3": ("L", 143, 512, 16),
    "ch4": ("L", 143, 1024, 32),
    "ch5": ("L", 215, 512, 16),
    "ch6": ("L", 215, 2048, 64),
    "ch7": ("L", 286, 1024, 32),
    "ch8": ("L", 286, 2048, 64),
    "ch9": ("L", 358, 1024, 32),
    "ch10": ("L", 358, 2048, 64),
    "ch11": ("L", 430, 1024, 32),
    "ch12": ("L", 430, 4096, 128),
    "ch13": ("L", 501, 1024, 32),
    "ch14": ("L", 501, 4096, 128),
    "ch15": ("L", 573, 2048, 64),
    "ch16": ("L", 573, 4096, 128),
    "ch17": ("L", 645, 2048, 64),
    "ch18": ("L", 645, 4096, 128),
    "ch19": ("L", 716, 2048, 64),
    "ch20": ("L", 716, 4096, 128),
    "ch21": ("M", 143, 512, 16),
    "ch22": ("M", 143, 1024, 32),
    "ch23": ("M", 286, 1024, 32),
    "ch24": ("M", 286, 2048, 64),
    "ch25": ("M", 430, 1024, 32),
    "ch26": ("M", 430, 4096, 128),
    "ch27": ("M", 573, 2048, 64),
    "ch28": ("M", 573, 4096, 128),
    "ch29": ("M", 716, 2048, 64),
    "ch30": ("M", 716, 4096, 128),
    "ch31": ("M", 860, 2048, 64),
    "ch32": ("M", 860, 8192, 256),
    "ch33": ("M", 1003, 2048, 64),
    "ch34": ("M", 1003, 8192, 256),
    "ch35": ("M", 1146, 4096, 128),
    "ch36": ("M", 1146, 8192, 256),
    "ch37": ("M", 1289, 4096, 128),
    "ch38": ("M", 1289, 8192, 256),
    "ch39": ("M", 1433, 4096, 128),
    "ch40": ("M", 1433, 8192, 256),
    "ch41": ("H", 286, 1024, 32),
    "ch42": ("H", 286, 2048, 64),
    "ch43": ("H", 573, 2048, 64),
    "ch44": ("H", 573, 4096, 128),
    "ch45": ("H", 860, 2048, 64),
    "ch46": ("H", 860, 8192, 256),
    "ch47": ("H", 1146, 4096, 128),
    "ch48": ("H", 1146, 8192, 256),
    "ch49": ("H", 1433, 4096, 128),
    "ch50": ("H", 1433, 8192, 256),
    "ch51": ("H", 1719, 4096, 128),
    "ch52": ("H", 1719, 16384, 512),
    "ch53": ("H", 2006, 4096, 128),
    "ch54": ("H", 2006, 16384, 512),
    "ch55": ("H", 2292, 8192, 256),
    "ch56": ("H", 2292, 16384, 512),
    "ch57": ("H", 2579, 8192, 256),
    "ch58": ("H", 2579, 16384, 512),
    "ch59": ("H", 2865, 8192, 256),
    "ch60": ("H", 2865, 16384, 512),
}


def generate_config(case_id, exp_type, template_path, output_path, db_url):

    benchmark = ""
    sf = 0
    series = ""
    tp_terminals = 0
    ap_terminals = 0

    if case_id in TPCC_CASES:
        benchmark = "tpcc"
        series, sf = TPCC_CASES[case_id]
        tp_terminals = sf * 10
    elif case_id in TPCH_CASES:
        benchmark = "tpch"
        series, sf = TPCH_CASES[case_id]
        tp_terminals = 1
    elif case_id in CH_CASES:
        benchmark = "chbenchmark"
        series, sf, tp_terminals, ap_terminals = CH_CASES[case_id]
    else:
        print(f"Error: Unknown Case ID {case_id}")
        sys.exit(1)

    time_seconds = int(os.getenv("BENCH_TIME_SEC", "1200"))


    try:
        tree = ET.parse(template_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing XML: {e}")
        sys.exit(1)


    url_tag = root.find("url")
    if url_tag is not None:
        url_tag.text = db_url

    username_tag = root.find("username")
    if username_tag is not None:
        username_tag.text = "gaussdb"

    password_tag = root.find("password")
    if password_tag is not None:
        password_tag.text = "CHANGE_ME"


    type_tag = root.find("type")
    if type_tag is None:
        type_tag = ET.SubElement(root, "type")
    type_tag.text = "POSTGRES"

    driver_tag = root.find("driver")
    if driver_tag is None:
        driver_tag = ET.SubElement(root, "driver")
    driver_tag.text = "org.postgresql.Driver"


    isolation_tag = root.find("isolation")
    if isolation_tag is None:
        isolation_tag = ET.SubElement(root, "isolation")
    isolation_tag.text = "TRANSACTION_READ_COMMITTED"

    # 5. Scale Factor
    sf_tag = root.find("scalefactor")
    if sf_tag is not None:
        sf_tag.text = str(sf)


    terminals_tag = root.find("terminals")
    works_tag = root.find("works")

    if benchmark == "chbenchmark":
        if terminals_tag is not None:
            root.remove(terminals_tag)

        t_tpcc = ET.SubElement(root, "terminals")
        t_tpcc.set("bench", "tpcc")
        t_tpcc.text = str(tp_terminals)

        t_ch = ET.SubElement(root, "terminals")
        t_ch.set("bench", "chbenchmark")
        t_ch.text = str(ap_terminals)

        if works_tag is not None:
            for work in works_tag.findall("work"):
                rate_tag = work.find("rate")
                if rate_tag is not None:
                    rate_tag.text = "unlimited"

                time_tag = work.find("time")
                if time_tag is not None:
                    time_tag.text = str(time_seconds)
    else:
        # TPCC / TPCH
        if terminals_tag is not None:
            terminals_tag.text = str(tp_terminals)

        if works_tag is not None:
            for work in works_tag.findall("work"):
                time_tag = work.find("time")
                if time_tag is not None:
                    time_tag.text = str(time_seconds)


                rate_tag = work.find("rate")
                if rate_tag is not None:
                    rate_tag.text = "unlimited"


    tree.write(output_path, encoding="UTF-8", xml_declaration=True)


    print(f"export RUN_BENCHMARK={benchmark}")
    print(f"export RUN_SF={sf}")
    print(f"export RUN_SERIES={series}")
    print(f"export RUN_TIME_SEC={time_seconds}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case_id", required=True)
    parser.add_argument("--exp_type", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--url", required=True)
    # Compatible with experiment_run.sh; currently only POSTGRES generation is implemented.
    parser.add_argument("--db-engine", default="pg")
    args = parser.parse_args()

    generate_config(args.case_id, args.exp_type, args.template, args.output, args.url)
