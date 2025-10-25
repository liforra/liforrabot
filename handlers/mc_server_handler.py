"""Handler for Minecraft server scanning functionality."""

import asyncio
import random
import json
import logging
from typing import Dict, List, Optional, Any
import aiohttp
import discord
from datetime import datetime

class MCServerHandler:
    """Handles Minecraft server scanning operations."""
    
    BASE_URL = "https://api.mcsrvstat.us/2/"
    
    def __init__(self, data_dir):
        """Initialize the Minecraft Server Handler."""
        self.data_dir = data_dir
        self.logger = logging.getLogger(__name__)
        self.history_file = data_dir / "mc_server_history.json"
        self.server_history = self._load_history()
    
    def _load_history(self) -> Dict[str, Any]:
        """Load server history from file."""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading server history: {e}")
        return {"servers": [], "player_history": {}}
    
    def _save_history(self):
        """Save server history to file."""
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.server_history, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving server history: {e}")
    
    async def search_servers(self, query: str = "") -> Dict[str, Any]:
        """Search for Minecraft servers.
        
        Args:
            query: Search query (can be IP, hostname, or search term)
            
        Returns:
            Dictionary containing server information or error message
        """
        if not query:
            return {"error": "Please provide a search query (IP, hostname, or search term)"}
            
        # If it looks like a direct IP/hostname, query it directly
        if '.' in query or ':' in query:
            return await self._query_server(query)
            
        # Otherwise, perform a search (this is a simplified example)
        return {"error": "Server search by name is not yet implemented. Please use an IP address or hostname."}
    
    async def get_random_server(self) -> Dict[str, Any]:
        """Get a random Minecraft server from history."""
        if not self.server_history["servers"]:
            return {"error": "No servers in history yet. Search for some servers first!"}
            
        random_server = random.choice(self.server_history["servers"])
        return await self._query_server(random_server)
    
    async def get_player_history(self, server_address: str) -> Dict[str, Any]:
        """Get player history for a specific server."""
        server_address = server_address.lower()
        if server_address in self.server_history["player_history"]:
            return {
                "server": server_address,
                "player_history": self.server_history["player_history"][server_address]
            }
        return {"error": f"No history found for server: {server_address}"}
    
    async def _query_server(self, address: str) -> Dict[str, Any]:
        """Query a Minecraft server using the mcsrvstat API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}{address}", timeout=10) as response:
                    if response.status != 200:
                        return {"error": f"Failed to fetch server info: {response.status}"}
                        
                    data = await response.json()
                    
                    # Update server history
                    if "online" in data and data["online"]:
                        self._update_server_history(address, data)
                    
                    return data
        except asyncio.TimeoutError:
            return {"error": "Server query timed out. The server might be offline or not responding."}
        except Exception as e:
            self.logger.error(f"Error querying server {address}: {e}")
            return {"error": f"An error occurred while querying the server: {str(e)}"}
    
    def _update_server_history(self, address: str, server_data: Dict[str, Any]):
        """Update server and player history."""
        address = address.lower()
        
        # Update server list
        if address not in self.server_history["servers"]:
            self.server_history["servers"].append(address)
        
        # Update player history
        if "players" in server_data and "list" in server_data["players"]:
            current_players = set(server_data["players"]["list"])
            timestamp = datetime.utcnow().isoformat()
            
            if address not in self.server_history["player_history"]:
                self.server_history["player_history"][address] = {}
            
            # Add new players to history
            for player in current_players:
                if player not in self.server_history["player_history"][address]:
                    self.server_history["player_history"][address][player] = {"first_seen": timestamp, "last_seen": timestamp}
                else:
                    self.server_history["player_history"][address][player]["last_seen"] = timestamp
            
            # Save the updated history
            self._save_history()
    
    @staticmethod
    def format_server_embed(server_data: Dict[str, Any], address: str) -> discord.Embed:
        """Format server data into a Discord embed."""
        if "error" in server_data:
            return discord.Embed(
                title="❌ Error",
                description=server_data["error"],
                color=discord.Color.red()
            )
        
        embed = discord.Embed(
            title=f"{server_data.get('motd', {}).get('clean', ['Unknown'])[0]}",
            description=f"`{address}`",
            color=discord.Color.green() if server_data.get("online", False) else discord.Color.red()
        )
        
        if "icon" in server_data:
            embed.set_thumbnail(url=f"data:image/png;base64,{server_data['icon']}")
        
        # Server status
        status = "🟢 Online" if server_data.get("online", False) else "🔴 Offline"
        embed.add_field(name="Status", value=status, inline=True)
        
        # Version
        if "version" in server_data:
            embed.add_field(name="Version", value=server_data["version"], inline=True)
        
        # Players
        if "players" in server_data:
            players = server_data["players"]
            online = players.get("online", 0)
            max_players = players.get("max", 0)
            player_list = "\n".join(players.get("list", []))
            
            embed.add_field(
                name=f"Players ({online}/{max_players})",
                value=player_list or "No players online",
                inline=False
            )
        
        # Plugins (if available)
        if "plugins" in server_data and "names" in server_data["plugins"] and server_data["plugins"]["names"]:
            plugins = ", ".join(server_data["plugins"]["names"][:10])  # Show first 10 plugins
            if len(server_data["plugins"]["names"]) > 10:
                plugins += f"... and {len(server_data['plugins']['names']) - 10} more"
            embed.add_field(name="Plugins", value=plugins, inline=False)
        
        # Additional info
        if "debug" in server_data:
            debug = server_data["debug"]
            if "ping" in debug:
                embed.set_footer(text=f"Ping: {debug['ping']}ms")
        
        return embed
    
    @staticmethod
    def format_player_history_embed(history_data: Dict[str, Any]) -> discord.Embed:
        """Format player history into a Discord embed."""
        if "error" in history_data:
            return discord.Embed(
                title="❌ Error",
                description=history_data["error"],
                color=discord.Color.red()
            )
        
        server = history_data["server"]
        player_history = history_data["player_history"]
        
        # Sort players by last seen (newest first)
        sorted_players = sorted(
            player_history.items(),
            key=lambda x: x[1]["last_seen"],
            reverse=True
        )
        
        # Format player list
        player_list = []
        for player, data in sorted_players[:25]:  # Limit to 25 players
            first_seen = datetime.fromisoformat(data["first_seen"]).strftime("%Y-%m-%d")
            last_seen = datetime.fromisoformat(data["last_seen"]).strftime("%Y-%m-%d")
            player_list.append(f"`{player}` - First: {first_seen}, Last: {last_seen}")
        
        embed = discord.Embed(
            title=f"Player History: {server}",
            description="\n".join(player_list) if player_list else "No player history found.",
            color=discord.Color.blue()
        )
        
        if len(sorted_players) > 25:
            embed.set_footer(text=f"Showing 25 of {len(sorted_players)} players. Use search for more specific results.")
        
        return embed
