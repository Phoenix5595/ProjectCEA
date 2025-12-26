#!/usr/bin/env python3
"""CAN Processor Monitor with incremental updates."""
import sys
import time
import subprocess
from datetime import datetime
from base_monitor import BaseMonitor, GREEN, YELLOW, RED, CYAN, BLUE, MAGENTA, NC

DB_CONFIG = "host=localhost dbname=cea_sensors user=cea_user password=Lenin1917"


class IncrementalMonitor(BaseMonitor):
    def __init__(self):
        super().__init__()
    
    def get_db_stats(self):
        """Get database statistics."""
        try:
            # Recent count
            result = subprocess.run(['psql', DB_CONFIG, '-t', '-A', '-c',
                "SELECT COUNT(*) FROM measurement WHERE time > NOW() - INTERVAL '5 minutes';"],
                capture_output=True, text=True, timeout=2)
            recent_count = result.stdout.strip() if result.returncode == 0 else "0"
            
            # Last minute
            result = subprocess.run(['psql', DB_CONFIG, '-t', '-A', '-c',
                "SELECT COUNT(*) FROM measurement WHERE time > NOW() - INTERVAL '1 minute';"],
                capture_output=True, text=True, timeout=2)
            last_minute = result.stdout.strip() if result.returncode == 0 else "0"
            
            return recent_count, last_minute
        except:
            return "0", "0"
    
    def draw_initial_screen(self):
        """Draw the initial screen layout."""
        print("\033[2J\033[H", end='')  # Clear screen and move to top
        print("==========================================")
        print("CAN Processor Monitor")
        print("==========================================")
        print("Last updated: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        print()
        
        # Service Status Section
        print("----------------------------------------")
        print(f"{CYAN}Service Status{NC}")
        print("----------------------------------------")
        print()  # Line for service status
        print()
        
        # CAN Bus Interface Section
        print("----------------------------------------")
        print(f"{CYAN}CAN Bus Interface{NC}")
        print("----------------------------------------")
        print()  # Line for CAN state
        print()
        
        # Redis Stream Section
        print("----------------------------------------")
        print(f"{CYAN}Redis Stream (sensor:raw){NC}")
        print("----------------------------------------")
        print()  # Line for stream length
        print()
        
        # Database Writing Section
        print("----------------------------------------")
        print(f"{CYAN}Database Writing{NC}")
        print("----------------------------------------")
        print()  # Line for DB stats
        print()
        
        # Recent CAN Messages Section
        print("----------------------------------------")
        print(f"{MAGENTA}Recent CAN Messages (from Redis Stream){NC}")
        print("----------------------------------------")
        # Reserve lines for messages (will be calculated dynamically)
        # Calculate available space based on used lines
        used_lines = 33  # Approximate lines used so far
        available_lines = self.max_lines - used_lines - 3  # -3 for footer
        max_message_lines = max(4, min(10, available_lines))  # At least 4, max 10
        for i in range(max_message_lines):
            print()
        print()
        print("==========================================")
        print("Press Ctrl+C to exit")
        sys.stdout.flush()
        
        # Calculate dynamic line positions
        current_line = 4  # Start after header
        current_line += 1  # timestamp
        current_line += 1  # blank
        
        # Service Status
        current_line += 2  # header + separator
        self.line_positions['service'] = current_line
        current_line += 2  # blank + separator
        
        # CAN Bus Interface
        current_line += 2  # header + separator
        self.line_positions['can'] = current_line
        current_line += 2  # blank + separator
        
        # Redis Stream
        current_line += 2  # header + separator
        self.line_positions['stream'] = current_line
        current_line += 2  # blank + separator
        
        # Database Writing
        current_line += 2  # header + separator
        self.line_positions['db'] = current_line
        current_line += 2  # blank + separator
        
        # Recent CAN Messages
        current_line += 2  # header + separator
        self.line_positions['messages'] = current_line
        # Recalculate max_message_lines based on actual available space
        used_lines = current_line
        available_lines = self.max_lines - used_lines - 3  # -3 for footer
        max_message_lines = max(4, min(10, available_lines))
        self.line_positions['max_message_lines'] = max_message_lines
        
        self.line_positions['timestamp'] = 4
        self.initial_draw = False
    
    def update_screen(self):
        """Update only changed values on screen."""
        is_first_update = self.initial_draw
        if is_first_update:
            self.draw_initial_screen()
        
        # Update timestamp (always update)
        self.update_line(self.line_positions['timestamp'], 
                        f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Update service status (force on first update)
        service_status = self.get_service_status()
        if is_first_update or service_status != self.prev_values.get('service_status'):
            status_text = f"can-processor.service: {GREEN}ACTIVE{NC}" if service_status == "active" else f"can-processor.service: {RED}INACTIVE{NC}"
            self.update_line(self.line_positions['service'], status_text)
            self.prev_values['service_status'] = service_status
        
        # Update CAN state (force on first update)
        can_state = self.get_can_state()
        if is_first_update or can_state != self.prev_values.get('can_state'):
            if can_state in ["UP", "UNKNOWN"]:
                state_text = f"can0: {GREEN}{can_state}{NC}"
            else:
                state_text = f"can0: {RED}{can_state}{NC}"
            self.update_line(self.line_positions['can'], state_text)
            self.prev_values['can_state'] = can_state
        
        # Update stream length (force on first update)
        stream_len = self.get_stream_length()
        if is_first_update or stream_len != self.prev_values.get('stream_len'):
            stream_text = f"Stream length: {stream_len} entries"
            if stream_len > 15000:
                stream_text += f" {RED}⚠️ WARNING: > 15000{NC}"
            elif stream_len > 10000:
                stream_text += f" {YELLOW}⚠️ WARNING: > 10000{NC}"
            self.update_line(self.line_positions['stream'], stream_text)
            self.prev_values['stream_len'] = stream_len
        
        # Update DB stats (force on first update)
        recent_count, last_minute = self.get_db_stats()
        db_key = f"{recent_count}:{last_minute}"
        if is_first_update or db_key != self.prev_values.get('db_stats'):
            try:
                rate = f"{float(last_minute)/60:.1f}" if last_minute and last_minute != "0" else "0.0"
            except (ValueError, TypeError):
                rate = "0.0"
            db_text = f"Last 5min: {recent_count} | Last 1min: {last_minute} | Rate: {rate}/sec"
            self.update_line(self.line_positions['db'], db_text)
            self.prev_values['db_stats'] = db_key
        
        # Update CAN messages (always update on first run)
        messages = self.get_stream_entries(count=10, entry_type='can')
        messages_key = str([(eid, f.get('id', '')) for eid, f in messages])
        if is_first_update or messages_key != self.prev_values.get('messages_key'):
            term_width, _ = self.get_terminal_size()
            col_width = (term_width - 3) // 2
            
            formatted = []
            for entry_id, fields in messages:
                formatted.append(self.format_can_message(entry_id, fields, col_width))
            
            max_message_lines = self.line_positions.get('max_message_lines', 10)
            self.render_two_columns(formatted, self.line_positions['messages'], 
                                   max_message_lines, col_width)
            
            self.prev_values['messages_key'] = messages_key


if __name__ == '__main__':
    monitor = IncrementalMonitor()
    monitor.run(update_interval=1)
