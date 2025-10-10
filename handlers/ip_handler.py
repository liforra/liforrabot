"""IP geolocation handling with IPv6 support and VPN detection."""

import json
import httpx
import re
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from utils.constants import COUNTRY_FLAGS, VPN_PROVIDERS
from utils.helpers import is_valid_ipv4, is_valid_ipv6, is_valid_ip


class IPHandler:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.ip_geo_file = data_dir / "ip_geo_data.json"
        self.ip_geo_data = {}
        self.load_ip_geo_data()

    def load_ip_geo_data(self):
        """Loads IP geolocation data from disk."""
        if self.ip_geo_file.exists():
            try:
                with open(self.ip_geo_file, "r", encoding="utf-8") as f:
                    self.ip_geo_data = json.load(f)
                print(
                    f"[{self.data_dir.name}] Loaded {len(self.ip_geo_data)} IP geo records"
                )
            except Exception as e:
                print(f"[{self.data_dir.name}] Error loading IP geo data: {e}")
                self.ip_geo_data = {}
        else:
            self.ip_geo_data = {}

    def save_ip_geo_data(self):
        """Saves IP geolocation data to disk."""
        try:
            with open(self.ip_geo_file, "w", encoding="utf-8") as f:
                json.dump(self.ip_geo_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[{self.data_dir.name}] Error saving IP geo data: {e}")

    def detect_vpn_provider(self, isp: str, org: str) -> Optional[str]:
        """
        Detects VPN provider from ISP or organization name.
        Returns the provider name if detected, None otherwise.
        """
        search_text = f"{isp or ''} {org or ''}".lower()

        for keyword, provider_name in VPN_PROVIDERS.items():
            if keyword in search_text:
                return provider_name

        return None

    async def fetch_ip_info(self, ip: str) -> Optional[Dict]:
        """Fetches IP information from ip-api.com (supports both IPv4 and IPv6)."""
        if not is_valid_ip(ip):
            return None

        fields_param = "query,status,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,proxy,hosting"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://ip-api.com/json/{ip}?fields={fields_param}", timeout=10
                )
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "success":
                    return data
                return None
        except Exception as e:
            print(f"[IPHandler] Error fetching IP info for {ip}: {e}")
            return None

    async def fetch_ip_info_batch(self, ips: List[str]) -> Dict[str, Dict]:
        """Fetches IP information for multiple IPs (supports both IPv4 and IPv6)."""
        if not ips:
            return {}

        results = {}
        fields_param = "query,status,country,countryCode,region,regionName,city,isp,org,as,proxy,hosting"

        # Process in batches of 100 (API limit)
        for i in range(0, len(ips), 100):
            batch = ips[i : i + 100]
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"http://ip-api.com/batch?fields={fields_param}",
                        json=batch,
                        timeout=30,
                    )
                    response.raise_for_status()
                    batch_results = response.json()

                    for data in batch_results:
                        if data.get("status") == "success":
                            ip = data.get("query")
                            results[ip] = data

                # Rate limiting: wait 2 seconds between batches
                if i + 100 < len(ips):
                    import asyncio

                    await asyncio.sleep(2)

            except Exception as e:
                print(f"[IPHandler] Error in batch IP fetch: {e}")

        return results

def format_ip_with_geo(self, ip: str) -> str:
    """Formats an IP address with flag, region, VPN detection, etc."""
    # IPv6 addresses don't work well in markdown links, so display them as plain text
    if is_valid_ipv6(ip):
        ip_display = f"`{ip}`"
    else:
        ip_url = f"https://whatismyipaddress.com/ip/{ip}"
        ip_display = f"[{ip}](<{ip_url}>)"

    if ip not in self.ip_geo_data:
        return f"🌐 {ip_display}"

    geo = self.ip_geo_data[ip]
    flag = COUNTRY_FLAGS.get(geo.get("countryCode", ""), "🌐")

    info_parts = []
    region_name = geo.get("regionName", geo.get("region", ""))
    if region_name:
        info_parts.append(region_name)

    # Check for VPN provider in ISP/org field
    vpn_provider = self.detect_vpn_provider(geo.get("isp", ""), geo.get("org", ""))

    if vpn_provider:
        info_parts.append(f"({vpn_provider})")
    elif geo.get("proxy"):
        info_parts.append("(Proxy)")

    if geo.get("hosting"):
        info_parts.append("(VPS)")

    final_string = f"{flag} {ip_display}"
    if info_parts:
        final_string += f" | {' '.join(info_parts)}"

    return final_string
