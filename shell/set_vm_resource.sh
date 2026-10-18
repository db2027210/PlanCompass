#!/usr/bin/env bash
set -e

# ==============================

# ==============================
VM_NAME="${1:-artifact-ubuntu20}"
CPU_NUM="${2:-24}"
MEM_SIZE="${3:-16G}"
READ_MB="${4:-30}"
WRITE_MB="${5:-30}"
DISK_DEV="${6:-vda}"

READ_BPS=$((READ_MB * 1024 * 1024))
WRITE_BPS=$((WRITE_MB * 1024 * 1024))

TMP_XML=""
VIRSH_TIMEOUT_SEC="${VM_RESOURCE_VIRSH_TIMEOUT_SEC:-60}"
VIRSH_XML_TIMEOUT_SEC="${VM_RESOURCE_XML_TIMEOUT_SEC:-90}"
VIRSH_XML_DEFINE_RETRIES="${VM_RESOURCE_XML_DEFINE_RETRIES:-2}"
LIBVIRT_DEFAULT_URI="${LIBVIRT_DEFAULT_URI:-qemu:///system}"

cleanup() {
    if [[ -n "${TMP_XML}" && -f "${TMP_XML}" ]]; then
        rm -f "${TMP_XML}"
    fi
}

trap cleanup EXIT

virsh_run_with_timeout() {
    local timeout_sec="$1"
    shift
    sudo env "LIBVIRT_DEFAULT_URI=${LIBVIRT_DEFAULT_URI}" timeout --kill-after=5s "${timeout_sec}s" virsh "$@"
}

virsh_run() {
    virsh_run_with_timeout "${VIRSH_TIMEOUT_SEC}" "$@"
}

virsh_state() {
    local vm_name="$1"
    virsh_run domstate "${vm_name}" | tr -d '\n' | xargs || true
}

update_cpu_topology() {
    local vm_name="$1"
    local cpu_num="$2"
    local threads="1"
    local sockets="1"
    local dies="1"
    local clusters="1"
    local cores="${cpu_num}"
    local current_topology=""

    current_topology="$(virsh_run dumpxml "${vm_name}" | grep -o "<topology[^>]*/>" | head -n 1 || true)"
    if [[ -n "${current_topology}" ]]; then
        local parsed_threads parsed_sockets parsed_dies parsed_clusters
        parsed_threads="$(echo "${current_topology}" | sed -n "s/.*threads='\([0-9]\+\)'.*/\1/p")"
        parsed_sockets="$(echo "${current_topology}" | sed -n "s/.*sockets='\([0-9]\+\)'.*/\1/p")"
        parsed_dies="$(echo "${current_topology}" | sed -n "s/.*dies='\([0-9]\+\)'.*/\1/p")"
        parsed_clusters="$(echo "${current_topology}" | sed -n "s/.*clusters='\([0-9]\+\)'.*/\1/p")"
        if [[ -n "${parsed_threads}" && "${parsed_threads}" -gt 0 ]]; then
            threads="${parsed_threads}"
        fi
        if [[ -n "${parsed_sockets}" && "${parsed_sockets}" -gt 0 ]]; then
            sockets="${parsed_sockets}"
        fi
        if [[ -n "${parsed_dies}" && "${parsed_dies}" -gt 0 ]]; then
            dies="${parsed_dies}"
        fi
        if [[ -n "${parsed_clusters}" && "${parsed_clusters}" -gt 0 ]]; then
            clusters="${parsed_clusters}"
        fi
    fi

    local divisor=$((threads * sockets * dies * clusters))
    if (( divisor <= 0 )); then
        divisor=1
        threads=1
        sockets=1
        dies=1
        clusters=1
    fi

    if (( cpu_num % divisor != 0 )); then
        echo " topology  sockets*dices*clusters*threads=${divisor} vcpu=${cpu_num} threads=1/sockets=1/dies=1/clusters=1" >&2
        threads=1
        sockets=1
        dies=1
        clusters=1
        divisor=1
    fi
    cores=$((cpu_num / divisor))

    TMP_XML="$(mktemp /tmp/${vm_name}.xml.XXXXXX)"
    if ! virsh_run dumpxml "${vm_name}" > "${TMP_XML}"; then
        echo " XML  ${VIRSH_TIMEOUT_SEC}s : ${vm_name}" >&2
        exit 1
    fi

    perl -0pi -e "s|<vcpu([^>]*)current='[^']*'([^>]*)>[0-9]+</vcpu>|<vcpu\1\2>${cpu_num}</vcpu>|g; s|<vcpu([^>]*)>[0-9]+</vcpu>|<vcpu\1>${cpu_num}</vcpu>|g" "${TMP_XML}"

    if grep -q "<topology" "${TMP_XML}"; then
        perl -0pi -e "s|<topology[^>]*/>|<topology sockets='${sockets}' dies='${dies}' clusters='${clusters}' cores='${cores}' threads='${threads}'/>|g" "${TMP_XML}"
    else
        perl -0pi -e "s|</cpu>|  <topology sockets='${sockets}' dies='${dies}' clusters='${clusters}' cores='${cores}' threads='${threads}'/>\n</cpu>|g" "${TMP_XML}"
    fi

    local define_attempt=1
    local define_succeeded=0
    while (( define_attempt <= VIRSH_XML_DEFINE_RETRIES )); do
        if virsh_run_with_timeout "${VIRSH_XML_TIMEOUT_SEC}" define "${TMP_XML}" >/dev/null; then
            define_succeeded=1
            break
        fi
        echo " XML  ${define_attempt}/${VIRSH_XML_DEFINE_RETRIES} : ${vm_name}" >&2
        define_attempt=$((define_attempt + 1))
        sleep 3
    done
    if (( define_succeeded != 1 )); then
        echo " XML  ${VIRSH_XML_TIMEOUT_SEC}s /: ${vm_name}" >&2
        exit 1
    fi
    rm -f "${TMP_XML}"
    TMP_XML=""

    echo " topology: sockets=${sockets} dies=${dies} clusters=${clusters} cores=${cores} threads=${threads}"
}

echo "======================================"
echo "Configuration"
echo "======================================"
echo "VM_NAME   = ${VM_NAME}"
echo "CPU_NUM   = ${CPU_NUM}"
echo "MEM_SIZE  = ${MEM_SIZE}"
echo "READ_MB   = ${READ_MB} MB/s"
echo "WRITE_MB  = ${WRITE_MB} MB/s"
echo "DISK_DEV  = ${DISK_DEV}"
echo "======================================"

# ==============================

# ==============================
if ! virsh_run dominfo "${VM_NAME}" >/dev/null 2>&1; then
    echo " ${VM_NAME} "
    echo ""
    virsh_run list --all || true
    exit 1
fi

# ==============================

# ==============================
echo
echo "[1/6] "
virsh_run domblklist "${VM_NAME}"

# ==============================

# ==============================
STATE="$(virsh_state "${VM_NAME}")"

if [[ "${STATE}" == "running" ]]; then
    echo
    echo "[2/6] ..."
    virsh_run shutdown "${VM_NAME}"

    for i in {1..60}; do
        sleep 2
        STATE="$(virsh_state "${VM_NAME}")"
        if [[ "${STATE}" == "shut off" ]]; then
            echo ""
            break
        fi
        echo "... ${i}"
    done

    STATE="$(virsh_state "${VM_NAME}")"
    if [[ "${STATE}" != "shut off" ]]; then
        if [[ "${VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT:-0}" == "1" ]]; then
            echo " VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT=1 "
            virsh_run destroy "${VM_NAME}" || true
            sleep 2
            STATE="$(virsh_state "${VM_NAME}")"
            if [[ "${STATE}" != "shut off" ]]; then
                echo " shut off=${STATE}"
                exit 1
            fi
        else
            echo ""
            echo ""
            echo "sudo virsh destroy ${VM_NAME}"
            exit 1
        fi
    fi
elif [[ "${STATE}" == "paused" || "${STATE}" == "pmsuspended" ]]; then
    echo
    echo "[2/6] ${STATE} shut off"
    virsh_run destroy "${VM_NAME}"
    sleep 2
    STATE="$(virsh_state "${VM_NAME}")"
    if [[ "${STATE}" != "shut off" ]]; then
        echo " shut off=${STATE}"
        exit 1
    fi
else
    echo
    echo "[2/6] ${STATE}"
fi

# ==============================

# ==============================
echo
echo "[3/6]  CPU  ${CPU_NUM}"

update_cpu_topology "${VM_NAME}" "${CPU_NUM}"

# ==============================

# ==============================
echo
echo "[4/6]  ${MEM_SIZE}"

virsh_run setmaxmem "${VM_NAME}" "${MEM_SIZE}" --config
virsh_run setmem "${VM_NAME}" "${MEM_SIZE}" --config

# ==============================

# ==============================
echo
echo "[5/6]  IO "
echo "read  = ${READ_MB} MB/s"
echo "write = ${WRITE_MB} MB/s"

virsh_run blkdeviotune "${VM_NAME}" "${DISK_DEV}" \
    --read-bytes-sec "${READ_BPS}" \
    --write-bytes-sec "${WRITE_BPS}" \
    --config

# ==============================

# ==============================
echo
echo "[6/6] "

STATE="$(virsh_state "${VM_NAME}")"
if [[ "${STATE}" == "shut off" ]]; then
    START_TIMEOUT_SEC="${VM_RESOURCE_START_TIMEOUT_SEC:-45}"
    if ! virsh_run_with_timeout "${START_TIMEOUT_SEC}" start "${VM_NAME}"; then
        START_STATE_WAIT_SEC="${VM_RESOURCE_START_STATE_WAIT_SEC:-120}"
        STATE=""
        for ((i = 0; i < START_STATE_WAIT_SEC; i += 3)); do
            sleep 3
            STATE="$(virsh_state "${VM_NAME}")"
            if [[ "${STATE}" == "running" || "${STATE}" == "paused" || "${STATE}" == "pmsuspended" ]]; then
                break
            fi
            echo " virsh start ... ${i}/${START_STATE_WAIT_SEC} =${STATE:-unknown}"
        done
        if [[ "${STATE}" == "running" ]]; then
            echo "virsh start  ${START_TIMEOUT_SEC}s  running"
        elif [[ "${STATE}" == "paused" || "${STATE}" == "pmsuspended" ]]; then
            echo "virsh start  ${STATE} resume"
            RESUME_TIMEOUT_SEC="${VM_RESOURCE_RESUME_TIMEOUT_SEC:-30}"
            if ! virsh_run_with_timeout "${RESUME_TIMEOUT_SEC}" resume "${VM_NAME}"; then
                echo "virsh resume  ${RESUME_TIMEOUT_SEC}s "
            fi
            RESUME_STATE_WAIT_SEC="${VM_RESOURCE_RESUME_STATE_WAIT_SEC:-60}"
            STATE=""
            for ((i = 0; i < RESUME_STATE_WAIT_SEC; i += 3)); do
                sleep 3
                STATE="$(virsh_state "${VM_NAME}")"
                if [[ "${STATE}" == "running" ]]; then
                    break
                fi
                echo " resume  running... ${i}/${RESUME_STATE_WAIT_SEC} =${STATE:-unknown}"
            done
            if [[ "${STATE}" == "running" ]]; then
                echo " resume  running"
            else
                echo "[ERROR]  resume  running: ${VM_NAME}=${STATE}" >&2
                exit 1
            fi
        else
            echo "[ERROR] : ${VM_NAME}" >&2
            echo "[ERROR] virsh start exceeded ${START_TIMEOUT_SEC}s or returned non-zero, state=${STATE}" >&2
            exit 1
        fi
    fi
else
    echo " ${STATE} start"
fi

sleep 3



LIVE_IO_TIMEOUT_SEC="${VM_RESOURCE_LIVE_IO_TIMEOUT_SEC:-30}"
if ! virsh_run_with_timeout "${LIVE_IO_TIMEOUT_SEC}" blkdeviotune "${VM_NAME}" "${DISK_DEV}" \
    --read-bytes-sec "${READ_BPS}" \
    --write-bytes-sec "${WRITE_BPS}" \
    --live; then
    echo "live IO  ${LIVE_IO_TIMEOUT_SEC}s  --config Configuration"
fi

echo
echo "======================================"
echo "Configuration"
echo "======================================"
echo
echo ""
virsh_run_with_timeout 10 dominfo "${VM_NAME}" || true

echo
echo " IO "
virsh_run_with_timeout 10 blkdeviotune "${VM_NAME}" "${DISK_DEV}" || true

echo
echo ""
echo "nproc"
echo "free -h"
