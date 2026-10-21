FLOWER_ROOM = "Flower Room"
RS485_BED_CAPACITY = 4
# Known CAN metric-channel bases whose location suffix is renamed on
# assignment. Anything else on a CAN device is a conflict, not a guess.
CAN_CHANNEL_BASES: tuple[str, ...] = (
    "dry_bulb",
    "wet_bulb",
    "rh",
    "vpd",
    "co2",
    "secondary_temp",
    "secondary_rh",
    "pressure",
    "water_level",
)

# (room, location_in_room) -> rack name created for the CAN position.
CAN_POSITION_RACKS: dict[str, str] = {"front": "Front", "back": "Back", "main": "Main"}


class SensorRegistryChannelsMixin:
    @staticmethod
    def _channel_base(name: str) -> str | None:
        """Return the known metric base for a channel name, else ``None``."""
        for base in sorted(CAN_CHANNEL_BASES, key=len, reverse=True):
            if name == base:
                return base
            if name.startswith(base) and name[len(base) :] in ("_f", "_b", "_v"):
                return base
        return None
