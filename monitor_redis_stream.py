#!/usr/bin/env python3
"""Redis Stream Monitor with incremental updates."""
import sys
import time
from datetime import datetime
from collections import defaultdict
from base_monitor import BaseMonitor, GREEN, YELLOW, RED, CYAN, BLUE, MAGENTA, NC


class IncrementalRedisMonitor(BaseMonitor):
    def __init__(self):
        super().__init__()
        self.stats_history = []  # For calculating rates
        self.max_stats_history = 30  # Keep last 30 updates (1 minute at 2s intervals)
        
    def calculate_stream_stats(self):
        """Calculate stream statistics by type and rates."""
        r = self.get_redis()
        if not r:
            return None
        
        try:
            # Get recent entries to analyze
            entries = r.xrevrange('sensor:raw', count=100)
            
            # Count by type
            type_counts = defaultdict(int)
            type_timestamps = defaultdict(list)
            
            for entry_id, fields in entries:
                entry_type = fields.get('type', 'unknown')
                type_counts[entry_type] += 1
                
                ts = fields.get('ts')
                if ts:
                    try:
                        timestamp = int(ts) / 1000
                        type_timestamps[entry_type].append(timestamp)
                    except:
                        pass
            
            # Calculate rates (entries per minute)
            current_time = datetime.now().timestamp()
            rates = {}
            for entry_type, timestamps in type_timestamps.items():
                if timestamps:
                    # Count entries in last minute
                    recent = [t for t in timestamps if current_time - t < 60]
                    rates[entry_type] = len(recent)
                else:
                    rates[entry_type] = 0
            
            # Calculate percentages
            total = sum(type_counts.values())
            percentages = {}
            if total > 0:
                for entry_type, count in type_counts.items():
                    percentages[entry_type] = (count / total) * 100
            
            return {
                'counts': dict(type_counts),
                'rates': rates,
                'percentages': percentages,
                'total': total
            }
        except Exception as e:
            return None
    
    def draw_initial_screen(self):
        """Draw the initial screen layout."""
        print("\033[2J\033[H", end='')  # Clear screen and move to top
        print("==========================================")
        print("Redis Stream Monitor - sensor:raw")
        print("==========================================")
        print("Last updated: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        print()
        
        # System Health Section
        print("----------------------------------------")
        print(f"{CYAN}System Health{NC}")
        print("----------------------------------------")
        print()  # Line for service status
        print()  # Line for CAN state
        print()
        
        # Stream Status Section
        print("----------------------------------------")
        print(f"{CYAN}Stream Status{NC}")
        print("----------------------------------------")
        print("Stream: sensor:raw (unified for CAN, Soil, Automation)")
        print()  # Line for stream length
        print()
        
        # Stream Statistics Section
        print("----------------------------------------")
        print(f"{CYAN}Stream Statistics{NC}")
        print("----------------------------------------")
        print()  # Line for statistics
        print()
        
        # Latest Entries Section
        print("----------------------------------------")
        print(f"{GREEN}Latest Entries (last 10){NC}")
        print("----------------------------------------")
        # Reserve lines for entries (will be calculated after we know line positions)
        for i in range(10):  # Reserve max, will be adjusted
            print()
        print()
        
        # Live Sensor Values Section
        print("----------------------------------------")
        print(f"{GREEN}Live Sensor Values (from Redis){NC}")
        print("----------------------------------------")
        print(f"{BLUE}Flower Room, back:{NC}")
        print()  # Line for back values
        print()
        print(f"{BLUE}Flower Room, front:{NC}")
        print()  # Line for front values
        print()
        print(f"{BLUE}Veg Room, main:{NC}")
        print()  # Line for veg values
        print()
        print("==========================================")
        print("Press Ctrl+C to exit")
        sys.stdout.flush()
        
        # Calculate dynamic line positions
        current_line = 4  # Start after header
        current_line += 1  # timestamp
        current_line += 1  # blank
        
        # System Health
        current_line += 2  # header + separator
        self.line_positions['service'] = current_line
        current_line += 1
        self.line_positions['can'] = current_line
        current_line += 2  # blank + separator
        
        # Stream Status
        current_line += 2  # header + separator
        current_line += 1  # description
        self.line_positions['stream_len'] = current_line
        current_line += 2  # blank + separator
        
        # Stream Statistics
        current_line += 2  # header + separator
        self.line_positions['stats'] = current_line
        current_line += 2  # blank + separator
        
        # Latest Entries
        current_line += 2  # header + separator
        self.line_positions['entries'] = current_line
        # Calculate max_entry_lines based on actual available space
        used_lines = current_line
        available_lines = self.max_lines - used_lines - 15  # -15 for sensor values section and footer
        max_entry_lines = max(4, min(10, available_lines))
        self.line_positions['max_entry_lines'] = max_entry_lines
        current_line += max_entry_lines + 1  # entries + blank
        
        # Live Sensor Values
        current_line += 2  # header + separator
        current_line += 1  # "Flower Room, back:" label
        self.line_positions['back'] = current_line
        current_line += 2  # blank + "Flower Room, front:" label
        self.line_positions['front'] = current_line
        current_line += 2  # blank + "Veg Room, main:" label
        self.line_positions['veg'] = current_line
        
        self.line_positions['timestamp'] = 4
        self.initial_draw = False
    
    def update_screen(self):
        """Update only changed values on screen."""
        is_first_update = self.initial_draw
        if is_first_update:
            self.draw_initial_screen()
        
        # Update timestamp
        self.update_line(self.line_positions['timestamp'], 
                        f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Update system health (force on first update)
        service_status = self.get_service_status()
        if is_first_update or service_status != self.prev_values.get('service_status'):
            status_text = f"can-processor.service: {GREEN}ACTIVE{NC}" if service_status == "active" else f"can-processor.service: {RED}INACTIVE{NC}"
            self.update_line(self.line_positions['service'], status_text)
            self.prev_values['service_status'] = service_status
        
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
            stream_text = f"Length: {stream_len} entries (MAXLEN: 100,000)"
            if stream_len > 75000:
                stream_text += f" {RED}⚠️ WARNING: > 75000{NC}"
            elif stream_len > 50000:
                stream_text += f" {YELLOW}⚠️ WARNING: > 50000{NC}"
            self.update_line(self.line_positions['stream_len'], stream_text)
            self.prev_values['stream_len'] = stream_len
        
        # Update stream statistics (force on first update)
        stats = self.calculate_stream_stats()
        if stats:
            stats_key = str(stats)
            if is_first_update or stats_key != self.prev_values.get('stats_key'):
                # Show counts and percentages (fit to terminal width)
                term_width, _ = self.get_terminal_size()
                type_names = {'can': 'CAN', 'soil': 'Soil', 'automation': 'Auto'}
                
                # Build stats text, ensuring it fits terminal width
                stats_parts = []
                for entry_type in ['can', 'soil', 'automation']:
                    count = stats['counts'].get(entry_type, 0)
                    pct = stats['percentages'].get(entry_type, 0)
                    rate = stats['rates'].get(entry_type, 0)
                    name = type_names.get(entry_type, entry_type.title())
                    stats_parts.append(f"{name}: {count} ({pct:.1f}%) | {rate}/min")
                
                stats_text = ' | '.join(stats_parts)
                # Truncate if too long
                if len(stats_text) > term_width - 2:
                    stats_text = stats_text[:term_width - 5] + "..."
                
                self.update_line(self.line_positions['stats'], stats_text)
                self.prev_values['stats_key'] = stats_key
        
        # Update entries (force on first update)
        entries = self.get_stream_entries(count=10)
        entries_key = str([(eid, f.get('type', '')) for eid, f in entries])
        if is_first_update or entries_key != self.prev_values.get('entries_key'):
            term_width, _ = self.get_terminal_size()
            col_width = (term_width - 3) // 2
            
            formatted = []
            for entry_id, fields in entries:
                formatted.append(self.format_stream_entry(entry_id, fields, col_width))
            
            max_entry_lines = self.line_positions.get('max_entry_lines', 10)
            self.render_two_columns(formatted, self.line_positions['entries'], 
                                  max_entry_lines, col_width)
            
            self.prev_values['entries_key'] = entries_key
        
        # Update live sensor values (force on first update)
        # Flower Room, back
        dry_b = self.redis_get("sensor:dry_bulb_b")
        wet_b = self.redis_get("sensor:wet_bulb_b")
        co2_b = self.redis_get("sensor:co2_b")
        pressure_b = self.redis_get("sensor:pressure_b")
        rh_b = self.redis_get("sensor:rh_b")
        vpd_b = self.redis_get("sensor:vpd_b")
        
        back_key = f"{dry_b}:{wet_b}:{co2_b}:{pressure_b}:{rh_b}:{vpd_b}"
        if is_first_update or back_key != self.prev_values.get('back_key'):
            rh_b_fmt = f"{float(rh_b):.3f}" if rh_b else "N/A"
            vpd_b_fmt = f"{float(vpd_b):.3f}" if vpd_b else "N/A"
            pressure_b_fmt = f"{float(pressure_b):.1f}" if pressure_b else "N/A"
            back_text = f"  Dry: {dry_b or 'N/A'}°C | Wet: {wet_b or 'N/A'}°C | CO2: {co2_b or 'N/A'}ppm | P: {pressure_b_fmt}hPa | RH: {rh_b_fmt}% | VPD: {vpd_b_fmt}kPa"
            self.update_line(self.line_positions['back'], back_text)
            self.prev_values['back_key'] = back_key
        
        # Flower Room, front
        dry_f = self.redis_get("sensor:dry_bulb_f")
        wet_f = self.redis_get("sensor:wet_bulb_f")
        co2_f = self.redis_get("sensor:co2_f")
        pressure_f = self.redis_get("sensor:pressure_f")
        rh_f = self.redis_get("sensor:rh_f")
        vpd_f = self.redis_get("sensor:vpd_f")
        
        front_key = f"{dry_f}:{wet_f}:{co2_f}:{pressure_f}:{rh_f}:{vpd_f}"
        if is_first_update or front_key != self.prev_values.get('front_key'):
            rh_f_fmt = f"{float(rh_f):.3f}" if rh_f else "N/A"
            vpd_f_fmt = f"{float(vpd_f):.3f}" if vpd_f else "N/A"
            pressure_f_fmt = f"{float(pressure_f):.1f}" if pressure_f else "N/A"
            front_text = f"  Dry: {dry_f or 'N/A'}°C | Wet: {wet_f or 'N/A'}°C | CO2: {co2_f or 'N/A'}ppm | P: {pressure_f_fmt}hPa | RH: {rh_f_fmt}% | VPD: {vpd_f_fmt}kPa"
            self.update_line(self.line_positions['front'], front_text)
            self.prev_values['front_key'] = front_key
        
        # Veg Room
        dry_v = self.redis_get("sensor:dry_bulb_v")
        wet_v = self.redis_get("sensor:wet_bulb_v")
        co2_v = self.redis_get("sensor:co2_v")
        pressure_v = self.redis_get("sensor:pressure_v")
        rh_v = self.redis_get("sensor:rh_v")
        vpd_v = self.redis_get("sensor:vpd_v")
        
        veg_key = f"{dry_v}:{wet_v}:{co2_v}:{pressure_v}:{rh_v}:{vpd_v}"
        if is_first_update or veg_key != self.prev_values.get('veg_key'):
            rh_v_fmt = f"{float(rh_v):.3f}" if rh_v else "N/A"
            vpd_v_fmt = f"{float(vpd_v):.3f}" if vpd_v else "N/A"
            pressure_v_fmt = f"{float(pressure_v):.1f}" if pressure_v else "N/A"
            veg_text = f"  Dry: {dry_v or 'N/A'}°C | Wet: {wet_v or 'N/A'}°C | CO2: {co2_v or 'N/A'}ppm | P: {pressure_v_fmt}hPa | RH: {rh_v_fmt}% | VPD: {vpd_v_fmt}kPa"
            self.update_line(self.line_positions['veg'], veg_text)
            self.prev_values['veg_key'] = veg_key


if __name__ == '__main__':
    monitor = IncrementalRedisMonitor()
    monitor.run(update_interval=1)
