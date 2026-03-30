#!/usr/bin/env python3
"""
Blackhole Routes Updater — с поддержкой whitelist
"""

import sys
import requests
import tempfile
import subprocess
import argparse
from ipaddress import ip_network, collapse_addresses
from pathlib import Path
from typing import List, Set


# ==================== НАСТРОЙКИ ====================

URLS: List[str] = [
    "https://raw.githubusercontent.com/C24Be/AS_Network_List/refs/heads/main/blacklists/blacklist-v4.txt",
    "https://raw.githubusercontent.com/C24Be/AS_Network_List/refs/heads/main/blacklists/blacklist-v6.txt"
]

OUTPUT_FILE = ""
PROTO_MARK = "blackhole"
WHITELIST_FILENAME = "whitelist.txt"

# ===================================================


def get_script_dir() -> Path:
    """Возвращает директорию, в которой лежит скрипт"""
    return Path(__file__).resolve().parent


def load_whitelist() -> Set[str]:
    """Загружает whitelist.txt из той же папки, где лежит скрипт"""
    script_dir = get_script_dir()
    whitelist_path = script_dir / WHITELIST_FILENAME

    if not whitelist_path.exists():
        print(f"ℹ️  Файл {WHITELIST_FILENAME} не найден. Whitelist отключён.")
        return set()

    try:
        with open(whitelist_path, encoding='utf-8') as f:
            lines = f.readlines()

        prefixes = set()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith(';'):
                continue
            try:
                ip_network(line, strict=False)
                prefixes.add(line)
            except Exception:
                continue

        print(f"✅ Загружен whitelist: {len(prefixes)} префиксов")
        return prefixes
    except Exception as e:
        print(f"⚠️  Ошибка при чтении {WHITELIST_FILENAME}: {e}")
        return set()


def download_prefixes(url: str) -> List[str]:
    try:
        print(f"↓ Скачиваем: {url}")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.text.splitlines()
    except Exception as e:
        print(f"✖ Ошибка скачивания {url}: {e}")
        return []


def parse_prefixes(lines: List[str]) -> Set[str]:
    prefixes = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        try:
            ip_network(line, strict=False)
            prefixes.add(line)
        except Exception:
            continue
    return prefixes


def networks_overlap(net1: str, net2: str) -> bool:
    """Проверяет, пересекаются ли два префикса (в любую сторону)"""
    try:
        n1 = ip_network(net1, strict=False)
        n2 = ip_network(net2, strict=False)
        return n1.overlaps(n2)
    except Exception:
        return False


def filter_blacklist_with_whitelist(blacklist: Set[str], whitelist: Set[str]) -> Set[str]:
    """Удаляет из blacklist все префиксы, которые пересекаются с любым префиксом из whitelist"""
    if not whitelist:
        return blacklist.copy()

    filtered = set()
    skipped = 0

    for bl_prefix in blacklist:
        should_skip = False
        for wl_prefix in whitelist:
            if networks_overlap(bl_prefix, wl_prefix):
                should_skip = True
                break
        if should_skip:
            skipped += 1
        else:
            filtered.add(bl_prefix)

    if skipped > 0:
        print(f"🛡️  Отфильтровано по whitelist: {skipped} префиксов")

    return filtered


def summarize_networks(prefix_set: Set[str]):
    ipv4 = []
    ipv6 = []
    for p in prefix_set:
        try:
            net = ip_network(p, strict=False)
            if net.version == 4:
                ipv4.append(net)
            else:
                ipv6.append(net)
        except Exception:
            continue

    summarized = []
    if ipv4:
        summarized.extend(collapse_addresses(ipv4))
    if ipv6:
        summarized.extend(collapse_addresses(ipv6))

    summarized.sort(key=lambda x: (x.version, x.network_address))
    return summarized


def generate_batch_commands(summarized):
    ipv4_cmds = []
    ipv6_cmds = []

    for net in summarized:
        if net.version == 4:
            ipv4_cmds.append(f"route replace blackhole {net} proto {PROTO_MARK}")
        else:
            ipv6_cmds.append(f"route replace blackhole {net} proto {PROTO_MARK}")

    return ipv4_cmds, ipv6_cmds


def flush_old_routes(dry_run: bool = False):
    print("🧹 Удаляем старые blackhole маршруты...")
    if dry_run:
        print(f"   [DRY-RUN] ip route flush proto {PROTO_MARK}")
        print(f"   [DRY-RUN] ip -6 route flush proto {PROTO_MARK}")
        return

    subprocess.run(f"ip route flush proto {PROTO_MARK}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(f"ip -6 route flush proto {PROTO_MARK}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_routes(ipv4_cmds: List[str], ipv6_cmds: List[str], dry_run: bool = False):
    if dry_run:
        print(f"\n[DRY-RUN] Будет применено IPv4: {len(ipv4_cmds)} | IPv6: {len(ipv6_cmds)} маршрутов")
        return

    print(f"Применяем {len(ipv4_cmds) + len(ipv6_cmds)} blackhole-маршрутов...")

    # IPv4
    if ipv4_cmds:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.batch', delete=True) as tmp:
            tmp.write('\n'.join(ipv4_cmds) + '\n')
            tmp.flush()
            try:
                subprocess.run(['ip', '-batch', tmp.name], check=True, capture_output=True)
                print("✅ IPv4 маршруты применены")
            except subprocess.CalledProcessError as e:
                print(f"❌ Ошибка IPv4: {e.stderr.decode().strip() if e.stderr else 'Неизвестно'}")

    # IPv6
    if ipv6_cmds:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.batch', delete=True) as tmp:
            tmp.write('\n'.join(ipv6_cmds) + '\n')
            tmp.flush()
            try:
                subprocess.run(['ip', '-6', '-batch', tmp.name], check=True, capture_output=True)
                print("✅ IPv6 маршруты применены")
            except subprocess.CalledProcessError as e:
                print(f"❌ Ошибка IPv6: {e.stderr.decode().strip() if e.stderr else 'Неизвестно'}")

    print("Применение завершено.")


def main():
    parser = argparse.ArgumentParser(description="Blackhole Routes Updater")
    parser.add_argument('--dry-run', action='store_true', help='Только показать действия, без изменения маршрутов')
    args = parser.parse_args()

    dry_run = args.dry_run

    print("=== Blackhole Routes Updater (с whitelist) ===\n")

    # Загружаем whitelist
    whitelist = load_whitelist()

    # Скачиваем и парсим blacklist
    all_prefixes: Set[str] = set()
    for url in URLS:
        lines = download_prefixes(url)
        parsed = parse_prefixes(lines)
        all_prefixes.update(parsed)
        print(f"   Найдено префиксов: {len(parsed)}")

    if not all_prefixes:
        print("Ошибка: не найдено префиксов.")
        sys.exit(1)

    print(f"\nВсего уникальных префиксов из blacklists: {len(all_prefixes)}")

    # Применяем whitelist-фильтрацию
    filtered_prefixes = filter_blacklist_with_whitelist(all_prefixes, whitelist)

    print(f"После фильтрации whitelist осталось: {len(filtered_prefixes)} префиксов")

    # Суммаризация
    summarized = summarize_networks(filtered_prefixes)
    print(f"После суммаризации осталось: {len(summarized)} префиксов")

    ipv4_cmds, ipv6_cmds = generate_batch_commands(summarized)

    flush_old_routes(dry_run)
    apply_routes(ipv4_cmds, ipv6_cmds, dry_run)

    # Сохраняем файл только если OUTPUT_FILE не пустая строка
    if OUTPUT_FILE:
        output_path = Path(OUTPUT_FILE)
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("#!/bin/bash\n")
                f.write("# Blackhole маршруты — сгенерировано автоматически\n\n")
                
                for cmd in ipv4_cmds:
                    f.write(f"ip {cmd}\n")
                for cmd in ipv6_cmds:
                    f.write(f"ip -6 {cmd}\n")

                f.write(f"\n# Удаление всех маршрутов одной командой:\n")
                f.write(f"# ip route flush proto {PROTO_MARK}\n")
                f.write(f"# ip -6 route flush proto {PROTO_MARK}\n")

            print(f"\nФайл сохранён: {OUTPUT_FILE}")
        except Exception as e:
            print(f"⚠️  Не удалось сохранить файл {OUTPUT_FILE}: {e}")
    else:
        print("\nℹ️  OUTPUT_FILE отключён (пустая строка) — файл не создан.")

    print(f"\n✅ Готово!")
    print(f"   IPv4: {len(ipv4_cmds)}")
    print(f"   IPv6: {len(ipv6_cmds)}")

    if not dry_run:
        print(f"\nУдалить все маршруты можно так:")
        print(f"   ip route flush proto {PROTO_MARK} && ip -6 route flush proto {PROTO_MARK}")


if __name__ == "__main__":
    main()