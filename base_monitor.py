#!/usr/bin/env python3
"""Base monitor class with shared functionality for terminal-based monitors."""
import redis
import json
import sys
import shutil
from datetime import datetime

# Colors
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
CYAN = '\033[0;36m'
BLUE = '\033[0;34m'
MAGENTA = '\033[0;35m'
NC = '\033[0m'  # No Color


class BaseMonitor:
    """Base class for terminal-based incremental monitors."""
    
    def __init__(self):
        self.prev_values = {}
        self.line_positions = {}
        self.initial_draw = True
        self.redis_client = None
        self.max_lines = self.get_max_lines()
        self.prev_terminal_size = self.get_terminal_size()
        
    def get_max_lines(self):
        """Get maximum lines available (terminal height - 50px, assuming ~2px per line)."""
        try:
            cols, lines = shutil.get_terminal_size()
            max_lines = lines - 25  # 50px / 2px per line = 25 lines
            return max(max_lines, 20)  # Minimum 20 lines
        except:
            return 40  # Default fallback
    
    def check_terminal_resize(self):
        """Check if terminal was resized and redraw if needed."""
        current_size = self.get_terminal_size()
        if current_size != self.prev_terminal_size:
            self.prev_terminal_size = current_size
            self.max_lines = self.get_max_lines()
            self.initial_draw = True  # Force redraw
            return True
        return False
    
    def get_terminal_size(self):
        """Get terminal width and height."""
        try:
            width, height = shutil.get_terminal_size()
            return width, height
        except:
            return 120, 40  # Default fallback
    
    def move_to_line(self, line):
        """Move cursor to specific line."""
        print(f"\033[{line};1H", end='')
        
    def clear_to_eol(self):
        """Clear from cursor to end of line."""
        print("\033[K", end='')
        
    def save_cursor(self):
        """Save cursor position."""
        print("\033[s", end='')
        
    def restore_cursor(self):
        """Restore cursor position."""
        print("\033[u", end='')
        
    def update_line(self, line, text):
        """Update a specific line with new text."""
        self.move_to_line(line)
        self.clear_to_eol()
        print(text, end='', flush=True)
        
    def get_redis(self):
        """Get Redis client (singleton pattern)."""
        if self.redis_client is None:
            try:
                self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
                self.redis_client.ping()
            except:
                return None
        return self.redis_client
    
    def redis_get(self, key):
        """Get value from Redis."""
        r = self.get_redis()
        if r:
            try:
                return r.get(key)
            except:
                return None
        return None
    
    def get_stream_length(self):
        """Get Redis stream length."""
        r = self.get_redis()
        if r:
            try:
                return r.xlen('sensor:raw')
            except:
                return 0
        return 0
    
    def get_stream_entries(self, count=10, entry_type=None):
        """Get recent stream entries, optionally filtered by type."""
        r = self.get_redis()
        if not r:
            return []
        try:
            entries = r.xrevrange('sensor:raw', count=count * 2 if entry_type else count)
            if entry_type:
                filtered = []
                for entry_id, fields in entries:
                    if fields.get('type') == entry_type:
                        filtered.append((entry_id, fields))
                        if len(filtered) >= count:
                            break
                return list(reversed(filtered))
            return list(reversed(entries))
        except:
            return []
    
    def format_timestamp(self, ts):
        """Format timestamp for display."""
        if ts:
            try:
                dt = datetime.fromtimestamp(int(ts) / 1000)
                return f"\033[0;90m{dt.strftime('%H:%M:%S')}\033[0m"
            except:
                pass
        return ""
    
    def render_two_columns(self, formatted_items, start_line, max_lines, col_width):
        """Render items in two columns with dynamic line positioning."""
        line_offset = 0
        max_pairs = max(1, max_lines // 5)
        items_to_show = formatted_items[:max_pairs * 2]
        
        for i in range(0, len(items_to_show), 2):
            if line_offset >= max_lines:
                break
                
            left = items_to_show[i]
            right = items_to_show[i + 1] if i + 1 < len(items_to_show) else []
            max_h = max(len(left), len(right))
            
            # Don't exceed available lines
            if line_offset + max_h > max_lines:
                max_h = max_lines - line_offset
            
            for j in range(max_h):
                self.move_to_line(start_line + line_offset)
                self.clear_to_eol()
                left_line = (left[j] if j < len(left) else '')[:col_width].ljust(col_width)
                if j < len(right):
                    right_line = right[j][:col_width]
                    print(f"{left_line} │ {right_line}", end='', flush=True)
                else:
                    print(left_line, end='', flush=True)
                line_offset += 1
            
            if i + 2 < len(items_to_show) and line_offset < max_lines:
                line_offset += 1
        
        # Clear remaining lines
        for i in range(line_offset, max_lines):
            self.move_to_line(start_line + i)
            self.clear_to_eol()
    
    def format_stream_entry(self, entry_id, fields, width=50):
        """Format a single stream entry for display (handles all types)."""
        lines = []
        entry_short = entry_id.split('-')[0][-4:] + '-' + entry_id.split('-')[1][:4]
        lines.append(f"{BLUE}ID: {entry_short}{NC}")
        
        entry_type = fields.get('type', 'unknown')
        type_colors = {'can': GREEN, 'soil': YELLOW, 'automation': MAGENTA}
        type_names = {'can': 'CAN', 'soil': 'Soil', 'automation': 'Auto'}
        color = type_colors.get(entry_type, '')
        name = type_names.get(entry_type, entry_type.title())
        lines.append(f"{color}{name}{NC}")
        
        if entry_type == 'can':
            decoded_str = fields.get('decoded', '{}')
            try:
                decoded = json.loads(decoded_str)
                msg_type = decoded.get('message_type', 'Unknown')
                node_id = decoded.get('node_id', '?')
                lines.append(f"N{node_id}: {msg_type}")
                
                sensor_lines = []
                if 'temp_dry_c' in decoded:
                    sensor_lines.append(f"Dry: {decoded['temp_dry_c']:.2f}°C")
                if 'temp_wet_c' in decoded:
                    sensor_lines.append(f"Wet: {decoded['temp_wet_c']:.2f}°C")
                if 'co2_ppm' in decoded:
                    sensor_lines.append(f"CO2: {decoded['co2_ppm']}ppm")
                if 'pressure_hpa' in decoded:
                    sensor_lines.append(f"P: {decoded['pressure_hpa']:.1f}hPa")
                if 'temperature_c' in decoded and msg_type == 'BME280':
                    sensor_lines.append(f"T: {decoded['temperature_c']:.2f}°C")
                if 'humidity_percent' in decoded and msg_type == 'BME280':
                    sensor_lines.append(f"RH: {decoded['humidity_percent']:.1f}%")
                
                if sensor_lines:
                    lines.append(' '.join(sensor_lines[:3]))
                    if len(sensor_lines) > 3:
                        lines.append(' '.join(sensor_lines[3:]))
            except:
                can_id = fields.get('id', 'N/A')
                lines.append(f"CAN: {can_id}")
        elif entry_type == 'soil':
            device = fields.get('device_name', 'N/A')
            sensor = fields.get('sensor_name', 'N/A')
            lines.append(f"{device}")
            lines.append(f"{sensor}")
        elif entry_type == 'automation':
            device = fields.get('device_name', 'N/A')
            lines.append(f"{device}")
        
        ts_str = self.format_timestamp(fields.get('ts'))
        if ts_str:
            lines.append(ts_str)
        
        return lines
    
    def format_can_message(self, entry_id, fields, width=50):
        """Format a single CAN message for display (alias for format_stream_entry)."""
        return self.format_stream_entry(entry_id, fields, width)
    
    def get_service_status(self, service_name='can-processor.service'):
        """Get systemd service status."""
        try:
            import subprocess
            result = subprocess.run(['systemctl', 'is-active', service_name], 
                                  capture_output=True, text=True, timeout=1)
            return result.stdout.strip() if result.returncode == 0 else "inactive"
        except:
            return "inactive"
    
    def get_can_state(self):
        """Get CAN bus interface state."""
        try:
            import subprocess
            result = subprocess.run(['ip', 'link', 'show', 'can0'], 
                                  capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'state' in line:
                        state = line.split('state')[1].split()[0]
                        return state
            return "NOT FOUND"
        except:
            return "UNKNOWN"
    
    def draw_initial_screen(self):
        """Override in subclasses to draw initial screen layout."""
        raise NotImplementedError("Subclasses must implement draw_initial_screen")
    
    def update_screen(self):
        """Override in subclasses to update screen content."""
        raise NotImplementedError("Subclasses must implement update_screen")
    
    def run(self, update_interval=2):
        """Main loop."""
        try:
            while True:
                import time
                # Check for terminal resize
                self.check_terminal_resize()
                self.update_screen()
                time.sleep(update_interval)
        except KeyboardInterrupt:
            print("\n\nExiting...")
            sys.exit(0)

