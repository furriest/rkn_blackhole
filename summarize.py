#!/usr/bin/env python3
"""
Blackhole Routes Updater — с поддержкой whitelist и локального blacklist
"""

import sys
import requests
import tempfile
import subprocess
import argparse
import configparser
from ipaddress import ip_network, collapse_addresses
from pathlib import Path
from typing import List, Set


def get_script_dir() -> Path:
    """Возвращает директорию, в которой лежит скрипт"""
    return Path(__file__).resolve().parent


def load_config() -> configparser.ConfigParser:
    """Загружает config.ini"""
    script_dir = get_script_dir()
    config_path = script_dir / "config.ini"

    config = configparser.ConfigParser()
    config.optionxform = str  # сохраняем регистр ключей

    if not config_path.exists():
        print(f"❌ Файл config.ini не найден! Создайте его по примеру.")
        sys.exit(1)

    try:
        config.read(config_path, encoding='utf-8')
        print(f"✅ Конфигурация загружена: {config_path}")
        return config
    except Exception as e:
        print(f"❌ Ошибка чтения config.ini: {e}")
        sys.exit(1)


def get_list_from_config(config: configparser.ConfigParser, section: str, key: str) -> List[str]:
    """Получает список строк из конфига (поддерживает многострочные значения)"""
    if not config.has_section(section) or not config.has_option(section, key):
        return []

    value = config.get(section, key).strip()
    if not value:
        return []

    # Разбиваем по строкам и очищаем
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines


def load_prefixes_from_file(filename: str, description: str) -> Set[str]:
    """Загружает префиксы из файла (whitelist или локальный blacklist)"""
    script_dir = get_script_dir()
    file_path = script_dir / filename

    if not file_path.exists():
        print(f"ℹ️  Файл {filename} не найден. {description} отключён.")
        return set()

    try:
        with open(file_path, encoding='utf-8') as f:
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

        print(f"✅ Загружен {description}: {len(prefixes)} префиксов")
        return prefixes
    except Exception as e:
        print(f"⚠️  Ошибка при чтении {filename}: {e}")
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
    """Проверяет, пересекаются ли два префикса"""
    try:
        n1 = ip_network(net1, strict=False)
        n2 = ip_network(net2, strict=False)
        return n1.overlaps(n2)
    except Exception:
        return False


def filter_blacklist_with_whitelist(blacklist: Set[str], whitelist: Set[str]) -> Set[str]:
    """Удаляет из blacklist префиксы, пересекающиеся с whitelist"""
    if not whitelist:
        return blacklist.copy()

    filtered = set()
    skipped = 0

    for bl_prefix in blacklist:
        should_skip = any(networks_overlap(bl_prefix, wl_prefix) for wl_prefix in whitelist)
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


def generate_batch_commands(summarized, proto_mark: str):
    ipv4_cmds = []
    ipv6_cmds = []

    for net in summarized:
        if net.version == 4:
            ipv4_cmds.append(f"route replace blackhole {net} proto {proto_mark}")
        else:
            ipv6_cmds.append(f"route replace blackhole {net} proto {proto_mark}")

    return ipv4_cmds, ipv6_cmds


def flush_old_routes(proto_mark: str, dry_run: bool = False):
    print("🧹 Удаляем старые blackhole маршруты...")
    if dry_run:
        print(f"   [DRY-RUN] ip route flush proto {proto_mark}")
        print(f"   [DRY-RUN] ip -6 route flush proto {proto_mark}")
        return

    subprocess.run(f"ip route flush proto {proto_mark}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(f"ip -6 route flush proto {proto_mark}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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

    print("=== Blackhole Routes Updater (с whitelist и локальным blacklist) ===\n")

    # Загружаем конфигурацию
    config = load_config()

    PROTO_MARK = config.get('General', 'PROTO_MARK', fallback='blackhole')
    OUTPUT_FILE = config.get('General', 'OUTPUT_FILE', fallback='')
    WHITELIST_FILENAME = config.get('General', 'WHITELIST_FILENAME', fallback='whitelist.txt')
    BLACKLIST_FILENAME = config.get('General', 'BLACKLIST_FILENAME', fallback='blacklist.txt')

    # Загружаем списки
    whitelist = load_prefixes_from_file(WHITELIST_FILENAME, "whitelist")
    local_blacklist = load_prefixes_from_file(BLACKLIST_FILENAME, "локальный blacklist")

    # Скачиваем префиксы из URL
    urls = get_list_from_config(config, 'Sources', 'URLS')
    remote_prefixes: Set[str] = set()

    for url in urls:
        lines = download_prefixes(url)
        parsed = parse_prefixes(lines)
        remote_prefixes.update(parsed)
        print(f"   Найдено префиксов: {len(parsed)}")

    print(f"\nВсего из удалённых источников: {len(remote_prefixes)} префиксов")

    # Объединяем все blacklist'ы
    all_blacklist = remote_prefixes.union(local_blacklist)
    print(f"После добавления локального blacklist всего: {len(all_blacklist)} префиксов")

    # Фильтрация по whitelist
    filtered_prefixes = filter_blacklist_with_whitelist(all_blacklist, whitelist)

    print(f"После фильтрации whitelist осталось: {len(filtered_prefixes)} префиксов")

    # Суммаризация
    summarized = summarize_networks(filtered_prefixes)
    print(f"После суммаризации осталось: {len(summarized)} префиксов")

    ipv4_cmds, ipv6_cmds = generate_batch_commands(summarized, PROTO_MARK)

    flush_old_routes(PROTO_MARK, dry_run)
    apply_routes(ipv4_cmds, ipv6_cmds, dry_run)

    # Сохранение в файл (если указан)
    if OUTPUT_FILE:
        output_path = get_script_dir() / OUTPUT_FILE
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
        print("\nℹ️  OUTPUT_FILE отключён — файл не создан.")

    print(f"\n✅ Готово!")
    print(f"   IPv4: {len(ipv4_cmds)}")
    print(f"   IPv6: {len(ipv6_cmds)}")

    if not dry_run:
        print(f"\nУдалить все маршруты можно так:")
        print(f"   ip route flush proto {PROTO_MARK} && ip -6 route flush proto {PROTO_MARK}")


if __name__ == "__main__":
    main()