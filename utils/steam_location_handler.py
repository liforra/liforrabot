"""Handles lookups for Steam's internal location codes."""

import json
from pathlib import Path
from typing import Optional, Dict

class SteamLocationHandler:
    def __init__(self, data_dir: Path = Path("utils")):
        self.countries_file = data_dir / "countries.json"
        self.states_file = data_dir / "countries-states.json"
        self.cities_file = data_dir / "countries-states-cities.json"
        
        self.locations: Dict[str, Dict] = {}
        self._load_and_process_data()

    def _load_and_process_data(self):
        """Loads and processes the location data from the three JSON files."""
        # Step 1: Load countries
        if not self.countries_file.exists():
            print("[Steam Location] WARNING: countries.json not found. Location lookups are disabled.")
            return
        
        try:
            with open(self.countries_file, "r", encoding="utf-8") as f:
                countries_data = json.load(f).get("countries", [])
            for country in countries_data:
                self.locations[country["code"]] = {"name": country["name"], "states": {}}
        except Exception as e:
            print(f"[Steam Location] ERROR: Failed to process countries.json: {e}")
            return
            
        print("[Steam Location] Loaded countries.")

        # Step 2: Load states and merge them into the main structure
        if not self.states_file.exists():
            print("[Steam Location] WARNING: countries-states.json not found. State/city lookups are disabled.")
            return

        try:
            with open(self.states_file, "r", encoding="utf-8") as f:
                states_data = json.load(f).get("countries", [])
            for country in states_data:
                if country["code"] in self.locations:
                    for state in country.get("states", []):
                        self.locations[country["code"]]["states"][state["code"]] = {"name": state["name"], "cities": {}}
        except Exception as e:
            print(f"[Steam Location] ERROR: Failed to process countries-states.json: {e}")
            return

        print("[Steam Location] Merged states.")

        # Step 3: Load cities and merge them
        if not self.cities_file.exists():
            print("[Steam Location] WARNING: countries-states-cities.json not found. City lookups are disabled.")
            return

        try:
            with open(self.cities_file, "r", encoding="utf-8") as f:
                cities_data = json.load(f).get("countries", [])
            for country in cities_data:
                if country["code"] in self.locations:
                    for state in country.get("states", []):
                        if state["code"] in self.locations[country["code"]]["states"]:
                            for city in state.get("cities", []):
                                self.locations[country["code"]]["states"][state["code"]]["cities"][str(city["id"])] = {"name": city["name"]}
        except Exception as e:
            print(f"[Steam Location] ERROR: Failed to process countries-states-cities.json: {e}")
            return
            
        print("[Steam Location] Merged cities. Database is ready.")


    def get_location_names(self, country_code: Optional[str], state_code: Optional[str], city_id: Optional[int]) -> Dict[str, Optional[str]]:
        """
        Looks up country, state, and city names from their codes using the processed data structure.
        """
        result = {"country": None, "state": None, "city": None}
        if not self.locations or not country_code:
            return result

        country_data = self.locations.get(country_code)
        if not country_data:
            return result
        
        result["country"] = country_data.get("name")
        
        if not state_code:
            return result
            
        state_data = country_data.get("states", {}).get(state_code)
        if not state_data:
            return result

        result["state"] = state_data.get("name")
        
        if not city_id:
            return result

        city_data = state_data.get("cities", {}).get(str(city_id))
        if city_data:
            result["city"] = city_data.get("name")
            
        return result