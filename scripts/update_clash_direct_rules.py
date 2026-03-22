#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 relay 相关上游域名自动写入 Clash DIRECT 规则并热重载。"""

from __future__ import annotations

import re
import socket
from pathlib import Path


RELAY_ROOT = Path("/home/user/图片/tushare-relay")
CLASH_CONFIG = Path("/home/user/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml")
MIHOMO_SOCKET = "/var/tmp/verge/verge-mihomo.sock"

STATIC_DIRECT_HOSTS = {
    "api.waditu.com",
    "waditu.com",
    "news.10jqka.com.cn",
    "10jqka.com.cn",
    "www.10jqka.com.cn",
}

IGNORE_HOSTS = {
    "localhost",
    "127.0.0.1",
    "{API_HOST}:{API_PORT}",
}


def extract_hosts(relay_root: Path) -> list[str]:
    pattern = re.compile(r'https?://([^/"\'\s]+)')
    hosts: set[str] = set(STATIC_DIRECT_HOSTS)
    for path in relay_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for host in pattern.findall(text):
            host = host.strip()
            if not host or host in IGNORE_HOSTS:
                continue
            hosts.add(host)
    return sorted(hosts)


def build_direct_rules(hosts: list[str]) -> list[str]:
    rules = []
    for host in hosts:
        if host.count(".") >= 2:
            rules.append(f"- DOMAIN,{host},DIRECT")
            parts = host.split(".")
            rules.append(f"- DOMAIN-SUFFIX,{'.'.join(parts[-2:])},DIRECT")
        else:
            rules.append(f"- DOMAIN,{host},DIRECT")
    deduped = []
    seen = set()
    for rule in rules:
        if rule in seen:
            continue
        seen.add(rule)
        deduped.append(rule)
    return deduped


def update_clash_rules(config_path: Path, rules: list[str]) -> tuple[bool, int]:
    text = config_path.read_text(encoding="utf-8")
    match_line = "- MATCH,宝可梦"
    if match_line not in text:
        raise RuntimeError("未找到 Clash 配置里的 MATCH 规则，无法插入 DIRECT 规则")

    existing = set(line.strip() for line in text.splitlines())
    new_rules = [rule for rule in rules if rule not in existing]
    if not new_rules:
        return False, 0

    new_block = "\n".join(new_rules) + "\n" + match_line
    text = text.replace(match_line, new_block, 1)
    config_path.write_text(text, encoding="utf-8")
    return True, len(new_rules)


def reload_mihomo_via_socket(sock_path: str) -> str:
    request = (
        "PUT /configs?force=true HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 2\r\n"
        "\r\n"
        "{}"
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(sock_path)
        client.sendall(request.encode())
        response = client.recv(4096).decode(errors="ignore")
    return response.splitlines()[0] if response else ""


def main() -> int:
    hosts = extract_hosts(RELAY_ROOT)
    rules = build_direct_rules(hosts)
    changed, count = update_clash_rules(CLASH_CONFIG, rules)
    status = reload_mihomo_via_socket(MIHOMO_SOCKET)

    print(f"relay_hosts={len(hosts)}")
    print(f"direct_rules_total={len(rules)}")
    print(f"direct_rules_added={count if changed else 0}")
    print(f"mihomo_reload={status or 'unknown'}")
    for host in hosts:
        print(host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
