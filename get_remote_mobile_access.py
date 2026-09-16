"""
HM Algo 2.0 — Remote Mobile Access Link & Tunnel Helper.
Displays live public HTTPS URLs to connect to HM Algo 2.0 Dashboard from any smartphone globally.
"""
import urllib.request
import json
import subprocess
import time

def get_public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get("ip", "Unknown")
    except Exception:
        return "Unknown"

import os

def main():
    public_ip = get_public_ip()
    primary_url = "https://hm2026.serveousercontent.com"

    print("=" * 80)
    print("      HM Algo 2.0 -- WORLDWIDE REMOTE MOBILE ACCESS INSTRUCTIONS")
    print("=" * 80)
    print("\n1. Open this URL on your mobile phone browser from ANYWHERE in the world:\n")
    print(f"   URL: {primary_url}")
    print("\n2. If prompted for Endpoint IP / Tunnel Password:")
    print(f"   Enter IP: {public_ip}")
    print("\n3. Credentials to log into HM Algo 2.0 Dashboard on mobile:")
    print("   Username: admin")
    print("   Password: hm2026admin (or hm2026)")
    print("=" * 80)

if __name__ == "__main__":
    main()
