#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  export EXECUTION_SERVICE_FILE
  export CONSENSUS_SERVICE_FILE
  export COMMAND_LOG
  EXECUTION_SERVICE_FILE=$(mktemp)
  CONSENSUS_SERVICE_FILE=$(mktemp)
  COMMAND_LOG=$(mktemp)

  getNetworkConfig() {
    ip_current="127.0.0.1"
    interface_current="lo"
    network_current="127.0.0.0/8"
    export ip_current interface_current network_current
  }

  clear() { :; }
  sleep() { :; }
  sudo() {
    case "$1" in
      systemctl|service)
        echo "sudo $*" >> "$COMMAND_LOG"
        ;;
      *)
        "$@"
        ;;
    esac
  }
}

teardown() {
  rm -f "$EXECUTION_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$COMMAND_LOG"
}

write_caplin_execution_service() {
  cat > "$EXECUTION_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for HOODI

[Service]
ExecStart=/usr/local/bin/erigon --http.addr=127.0.0.1 --beacon.api.addr=127.0.0.1
EOF
}

@test "exposeRpcEL supports Erigon-Caplin execution RPC" {
  write_caplin_execution_service
  getClient

  exposeRpcEL <<< "yy"

  grep -q -- "--http.addr=0.0.0.0" "$EXECUTION_SERVICE_FILE"
}

@test "exposeRpcCL updates integrated Caplin REST on execution service" {
  write_caplin_execution_service
  getClient

  exposeRpcCL <<< "yy"
  grep -q -- "--beacon.api.addr=0.0.0.0" "$EXECUTION_SERVICE_FILE"

  exposeRpcCL <<< "yn"
  grep -q -- "--beacon.api.addr=127.0.0.1" "$EXECUTION_SERVICE_FILE"
}
