"""Alts data processing and management."""

import json
import re
import httpx
from pathlib import Path
from typing import Dict, Set, Optional
from datetime import datetime, timedelta
from utils.helpers import is_valid_ipv4, is_valid_ipv6


class AltsHandler:
    def __init__(self, data_dir: Path, clean_spigey: bool):
        self.data_dir = data_dir
        self.alts_data_file = data_dir / "alts_data.json"
        self.alts_data = {}
        self.clean_spigey = clean_spigey
        self.alts_command_counter = 0
        self._last_alts_fetch: Optional[datetime] = None
        self._cached_remote_data: Optional[Dict] = None
        self.alts_override_file = data_dir / "alts_override.json"
        self.alts_overrides = self.load_alts_overrides()

    def load_and_preprocess_alts_data(self):
        """Loads and preprocesses alts data with Spigey isolation if enabled."""
        if not self.alts_data_file.exists():
            self.alts_data = {}
            return

        print("[Alts Pre-processor] Loading raw data file...")
        try:
            with open(self.alts_data_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            print(f"[{self.data_dir.name}] Error loading raw alts data: {e}")
            self.alts_data = {}
            return

        if (
            "Spigey" in raw_data
            and isinstance(raw_data["Spigey"], dict)
            and "alts" in raw_data["Spigey"]
        ):
            print(
                "[Alts Pre-processor] Data appears to be already structured. Loading normally."
            )
            self.load_alts_data()
            return

        if self.clean_spigey:
            print(
                "[Alts Pre-processor] Raw data detected. `clean-spigey` is true. Starting Spigey data isolation process..."
            )
            spigey_identities = {
                user
                for users in raw_data.values()
                for user in users
                if user.startswith("...")
            }
            spigey_identities.add("Spigey")
            spigey_identities.add("911WasMyFault")
            spigey_identities.add(".ASW_<h1>nigger</h1>")

            true_spigey_ips = {"193.32.248.162"}
            for identifier, users in raw_data.items():
                user_set = set(users)
                if user_set and user_set.issubset(spigey_identities):
                    true_spigey_ips.add(identifier)
            print(
                f"[Alts Pre-processor] Identified {len(true_spigey_ips)} trusted Spigey identifiers."
            )

            cleaned_raw_data = {}
            isolated_spigey_alts = set()

            for identifier, users in raw_data.items():
                if identifier in true_spigey_ips:
                    isolated_spigey_alts.update(users)
                else:
                    cleaned_users = [
                        user for user in users if user not in spigey_identities
                    ]
                    if cleaned_users:
                        cleaned_raw_data[identifier] = cleaned_users

            data_to_process = cleaned_raw_data
        else:
            print(
                "[Alts Pre-processor] Raw data detected. `clean-spigey` is false. Using standard processing."
            )
            data_to_process = raw_data

        print("[Alts Pre-processor] Processing data...")
        timestamp = datetime.now().isoformat()
        for identifier, users in data_to_process.items():
            all_users_in_group = set(users)
            all_ips_in_group = (
                {identifier}
                if is_valid_ipv4(identifier) or is_valid_ipv6(identifier)
                else set()
            )

            for user in list(all_users_in_group):
                if user in self.alts_data:
                    all_users_in_group.update(self.alts_data[user].get("alts", set()))
                    all_ips_in_group.update(self.alts_data[user].get("ips", set()))

            for user in all_users_in_group:
                if user not in self.alts_data:
                    self.alts_data[user] = {
                        "alts": set(),
                        "ips": set(),
                        "first_seen": timestamp,
                        "last_updated": timestamp,
                    }
                self.alts_data[user]["alts"].update(all_users_in_group)
                self.alts_data[user]["ips"].update(all_ips_in_group)
                self.alts_data[user]["last_updated"] = timestamp

        if self.clean_spigey:
            print("[Alts Pre-processor] Injecting isolated Spigey data...")
            final_spigey_group = {
                "alts": isolated_spigey_alts.union(spigey_identities),
                "ips": true_spigey_ips,
                "first_seen": timestamp,
                "last_updated": timestamp,
            }
            for user in final_spigey_group["alts"]:
                self.alts_data[user] = final_spigey_group

        if self.apply_overrides(timestamp):
            print("[Alts Pre-processor] Override rules applied.")

        print(
            f"[Alts Pre-processor] Process complete. Loaded {len(self.alts_data)} total records."
        )
        self.save_alts_data()
        print("[Alts Pre-processor] Cleaned and structured data has been saved.")

    def load_alts_data(self):
        """Loads structured alts data from disk."""
        if self.alts_data_file.exists():
            try:
                with open(self.alts_data_file, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                if "Spigey" in loaded_data and "alts" in loaded_data["Spigey"]:
                    self.alts_data = {
                        username: {
                            "alts": set(data.get("alts", [])),
                            "ips": set(data.get("ips", [])),
                            "first_seen": data.get("first_seen", ""),
                            "last_updated": data.get("last_updated", ""),
                        }
                        for username, data in loaded_data.items()
                    }
                else:
                    self.alts_data = {}
                print(
                    f"[{self.data_dir.name}] Loaded {len(self.alts_data)} structured alt records"
                )
            except Exception as e:
                print(f"[{self.data_dir.name}] Error loading alts data: {e}")
                self.alts_data = {}
        else:
            self.alts_data = {}

        if self.apply_overrides():
            self.save_alts_data()

    def save_alts_data(self):
        """Saves alts data to disk."""
        try:
            data_to_save = {
                username: {
                    "alts": sorted(list(data["alts"])),
                    "ips": sorted(list(data["ips"])),
                    "first_seen": data["first_seen"],
                    "last_updated": data["last_updated"],
                }
                for username, data in self.alts_data.items()
            }
            with open(self.alts_data_file, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[{self.data_dir.name}] Error saving alts data: {e}")

    def load_alts_overrides(self) -> Dict[str, Dict[str, Set[str]]]:
        """Loads override definitions from disk."""
        overrides: Dict[str, Dict[str, Set[str]]] = {}
        if not self.alts_override_file.exists():
            return overrides

        try:
            with open(self.alts_override_file, "r", encoding="utf-8") as f:
                raw_overrides = json.load(f)
        except Exception as e:
            print(f"[Alts Override] Error loading overrides: {e}")
            return overrides

        if not isinstance(raw_overrides, dict):
            print("[Alts Override] Invalid override format. Expected a JSON object at root.")
            return overrides

        for main_name, entry in raw_overrides.items():
            if not isinstance(entry, dict):
                continue

            alt_values = entry.get("alts", [])
            if not isinstance(alt_values, list):
                alt_values = []
            alt_set = {str(name) for name in alt_values if isinstance(name, str)}

            ips_specified = "ips" in entry
            ip_values = entry.get("ips", []) if ips_specified else []
            if not isinstance(ip_values, list):
                ip_values = []
            ip_set = {
                ip
                for ip in ip_values
                if isinstance(ip, str) and (is_valid_ipv4(ip) or is_valid_ipv6(ip))
            }

            overrides[str(main_name)] = {
                "alts": alt_set,
                "ips": ip_set,
                "ips_specified": ips_specified,
            }

        return overrides

    def apply_overrides(self, timestamp: Optional[str] = None) -> bool:
        """Applies override rules to isolate specified accounts and IPs."""
        self.alts_overrides = self.load_alts_overrides()
        if not self.alts_overrides:
            return False

        changed = False
        timestamp = timestamp or datetime.now().isoformat()

        for main_name, override in self.alts_overrides.items():
            override_alts = set(override.get("alts", set()))
            override_alts.add(main_name)

            existing_first_seen = []
            existing_ips: Set[str] = set()
            for name in override_alts:
                record = self.alts_data.get(name)
                if record:
                    if record.get("first_seen"):
                        existing_first_seen.append(record.get("first_seen"))
                    existing_ips.update(record.get("ips", set()))

            if override.get("ips_specified"):
                override_ips = set(override.get("ips", set()))
            else:
                override_ips = set(existing_ips)

            override_ips = {
                ip
                for ip in override_ips
                if is_valid_ipv4(ip) or is_valid_ipv6(ip)
            }

            first_seen_candidates = [fs for fs in existing_first_seen if fs]
            first_seen = min(first_seen_candidates) if first_seen_candidates else timestamp

            for username, record in list(self.alts_data.items()):
                if username in override_alts:
                    continue

                original_alts = set(record.get("alts", set()))
                original_ips = set(record.get("ips", set()))

                updated_alts = original_alts - override_alts
                if username not in updated_alts:
                    updated_alts.add(username)
                updated_ips = original_ips - override_ips

                if updated_alts != original_alts or updated_ips != original_ips:
                    changed = True
                    record["alts"] = updated_alts
                    record["ips"] = updated_ips
                    if not record.get("first_seen"):
                        record["first_seen"] = timestamp
                    record["last_updated"] = timestamp

            override_alts_final = set(override_alts)
            override_ips_final = set(override_ips)

            for name in override_alts_final:
                existing = self.alts_data.get(name)
                previous_alts = existing.get("alts") if existing else set()
                previous_ips = existing.get("ips") if existing else set()
                previous_first_seen = existing.get("first_seen") if existing else None

                if (
                    existing is None
                    or previous_alts != override_alts_final
                    or previous_ips != override_ips_final
                    or previous_first_seen != first_seen
                    or existing.get("last_updated") != timestamp
                ):
                    changed = True

                self.alts_data[name] = {
                    "alts": set(override_alts_final),
                    "ips": set(override_ips_final),
                    "first_seen": first_seen,
                    "last_updated": timestamp,
                }

        return changed

    def parse_alts_response(self, content: str) -> Optional[Dict]:
        """Parses Asteroide bot response."""
        try:
            main_match = re.search(r"^(\S+) has \d+ alts:", content, re.MULTILINE)
            if not main_match:
                return None
            main_user = main_match.group(1)
            alts = re.findall(r"^-> (\S+)$", content, re.MULTILINE) or [main_user]
            ip_section_match = re.search(
                r"On \d+ IPs:(.*?)(?=\n\n|\Z)", content, re.DOTALL
            )

            # Updated to support both IPv4 and IPv6
            ips = []
            if ip_section_match:
                # IPv4 pattern
                ipv4_ips = re.findall(
                    r"-> ((?:\d{1,3}\.){3}\d{1,3})", ip_section_match.group(1)
                )
                # IPv6 pattern (simplified, matches common formats)
                ipv6_ips = re.findall(
                    r"-> ([0-9a-fA-F:]+(?::[0-9a-fA-F]+)*)", ip_section_match.group(1)
                )
                ips = ipv4_ips + [ip for ip in ipv6_ips if is_valid_ipv6(ip)]

            return {
                "main_user": main_user,
                "alts": alts,
                "ips": ips,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            print(f"[AltsHandler] Error parsing alts response: {e}")
            return None

    def store_alts_data(self, parsed_data: Dict):
        """Stores parsed alts data."""
        main_user = parsed_data["main_user"]
        all_users_in_group = set(parsed_data.get("alts", []))
        all_ips_in_group = set(parsed_data.get("ips", []))

        for user in list(all_users_in_group):
            if user in self.alts_data:
                all_users_in_group.update(self.alts_data[user].get("alts", set()))
                all_ips_in_group.update(self.alts_data[user].get("ips", set()))

        for user in all_users_in_group:
            if user not in self.alts_data:
                self.alts_data[user] = {
                    "alts": set(),
                    "ips": set(),
                    "first_seen": parsed_data["timestamp"],
                    "last_updated": parsed_data["timestamp"],
                }

            self.alts_data[user]["alts"].update(all_users_in_group)
            self.alts_data[user]["ips"].update(all_ips_in_group)
            self.alts_data[user]["last_updated"] = parsed_data["timestamp"]

        self.apply_overrides(parsed_data["timestamp"])
        self.save_alts_data()
        print(f"[AltsHandler] Updated alts data for group starting with {main_user}")

    async def refresh_alts_data(self, alts_refresh_url: str, ip_handler, http_client: Optional[httpx.AsyncClient] = None) -> bool:
        """Refreshes alts data from remote source."""
        if not alts_refresh_url:
            print("[Alts Refresh] URL not configured.")
            return False

        recent_cache_valid = (
            self._cached_remote_data is not None
            and self._last_alts_fetch is not None
            and (datetime.now() - self._last_alts_fetch) < timedelta(seconds=5)
        )

        if recent_cache_valid:
            print("[Alts Refresh] Using cached remote data (recent fetch).")
            remote_data = self._cached_remote_data
        else:
            client = http_client or httpx.AsyncClient()
            own_client = http_client is None

            print("[Alts Refresh] Fetching remote data...")
            try:
                res = await client.get(alts_refresh_url, timeout=30)
                res.raise_for_status()
                remote_data = res.json()
                self._cached_remote_data = remote_data
                self._last_alts_fetch = datetime.now()
            except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
                print(f"[Alts Refresh] Failed to fetch or parse remote data: {e}")
                if own_client:
                    await client.aclose()
                return False
            finally:
                if own_client:
                    await client.aclose()

        timestamp = datetime.now().isoformat()
        update_count = 0

        if self.clean_spigey:
            print(
                "[Alts Refresh] `clean-spigey` is true. Pre-processing remote data to isolate Spigey..."
            )

            spigey_identities = {
                user
                for users in remote_data.values()
                for user in users
                if user.startswith("...")
            }
            spigey_identities.add("Spigey")
            spigey_identities.add("911WasMyFault")
            spigey_identities.add(".ASW_<h1>nigger</h1>")

            true_spigey_ips = {"193.32.248.162"}
            for identifier, users in remote_data.items():
                if set(users).issubset(spigey_identities):
                    true_spigey_ips.add(identifier)

            cleaned_remote_data = {}
            remote_spigey_alts = set()
            for identifier, users in remote_data.items():
                if identifier in true_spigey_ips:
                    remote_spigey_alts.update(users)
                else:
                    cleaned_users = [
                        user for user in users if user not in spigey_identities
                    ]
                    if cleaned_users:
                        cleaned_remote_data[identifier] = cleaned_users

            data_to_process = cleaned_remote_data

            print("[Alts Refresh] Merging sanitized remote data for all other users...")
            for identifier, users in data_to_process.items():
                update_count += 1
                all_users_in_group = set(users)
                all_ips_in_group = (
                    {identifier}
                    if is_valid_ipv4(identifier) or is_valid_ipv6(identifier)
                    else set()
                )

                for user in list(all_users_in_group):
                    if user in self.alts_data and user not in spigey_identities:
                        all_users_in_group.update(
                            self.alts_data[user].get("alts", set())
                        )
                        all_ips_in_group.update(self.alts_data[user].get("ips", set()))

                for user in all_users_in_group:
                    if user not in self.alts_data:
                        self.alts_data[user] = {
                            "alts": set(),
                            "ips": set(),
                            "first_seen": timestamp,
                            "last_updated": timestamp,
                        }
                    self.alts_data[user]["alts"].update(all_users_in_group)
                    self.alts_data[user]["ips"].update(all_ips_in_group)
                    self.alts_data[user]["last_updated"] = timestamp

            print("[Alts Refresh] Injecting final isolated Spigey data...")
            spigey_base_record = self.alts_data.get(
                "Spigey", {"alts": set(), "ips": set(), "first_seen": timestamp}
            )
            final_spigey_alts = (
                spigey_base_record["alts"]
                .union(remote_spigey_alts)
                .union(spigey_identities)
            )
            final_spigey_ips = spigey_base_record["ips"].union(true_spigey_ips)
            final_spigey_group = {
                "alts": final_spigey_alts,
                "ips": final_spigey_ips,
                "first_seen": spigey_base_record["first_seen"],
                "last_updated": timestamp,
            }
            for user in final_spigey_alts:
                self.alts_data[user] = final_spigey_group
        else:
            print("[Alts Refresh] `clean-spigey` is false. Merging all remote data...")
            for identifier, users in remote_data.items():
                update_count += 1
                all_users_in_group = set(users)
                all_ips_in_group = (
                    {identifier}
                    if is_valid_ipv4(identifier) or is_valid_ipv6(identifier)
                    else set()
                )

                for user in list(all_users_in_group):
                    if user in self.alts_data:
                        all_users_in_group.update(
                            self.alts_data[user].get("alts", set())
                        )
                        all_ips_in_group.update(self.alts_data[user].get("ips", set()))

                for user in all_users_in_group:
                    if user not in self.alts_data:
                        self.alts_data[user] = {
                            "alts": set(),
                            "ips": set(),
                            "first_seen": timestamp,
                            "last_updated": timestamp,
                        }
                    self.alts_data[user]["alts"].update(all_users_in_group)
                    self.alts_data[user]["ips"].update(all_ips_in_group)
                    self.alts_data[user]["last_updated"] = timestamp

        overrides_changed = self.apply_overrides(timestamp)

        # Fetch IP geo data for new IPs
        print("[Alts Refresh] Fetching IP geolocation data...")
        all_ips = set()
        for data in self.alts_data.values():
            all_ips.update(data.get("ips", set()))

        new_ips = [ip for ip in all_ips if ip not in ip_handler.ip_geo_data]

        if new_ips:
            print(f"[Alts Refresh] Fetching geo data for {len(new_ips)} new IPs...")
            geo_results = await ip_handler.fetch_ip_info_batch(new_ips)

            for ip, geo_data in geo_results.items():
                ip_handler.ip_geo_data[ip] = {
                    "country": geo_data.get("country"),
                    "countryCode": geo_data.get("countryCode"),
                    "region": geo_data.get("region"),
                    "regionName": geo_data.get("regionName"),
                    "city": geo_data.get("city"),
                    "isp": geo_data.get("isp"),
                    "org": geo_data.get("org"),
                    "proxy": geo_data.get("proxy", False),
                    "hosting": geo_data.get("hosting", False),
                    "last_updated": timestamp,
                }

            ip_handler.save_ip_geo_data()
            print(f"[Alts Refresh] Saved geo data for {len(geo_results)} IPs")

        if update_count > 0 or overrides_changed:
            self.save_alts_data()

        print(f"[Alts Refresh] Successfully merged data for {update_count} groups.")
        return True
