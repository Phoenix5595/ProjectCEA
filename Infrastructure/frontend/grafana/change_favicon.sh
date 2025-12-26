#!/bin/bash
# Change Grafana Favicon
# This script helps you replace the default Grafana favicon with a custom one

echo "=========================================="
echo "Grafana Favicon Replacement"
echo "=========================================="
echo ""

# Check if custom favicon path provided
CUSTOM_FAVICON="${1:-}"

if [ -z "$CUSTOM_FAVICON" ]; then
    echo "Usage: $0 <path-to-your-favicon.png>"
    echo ""
    echo "Example:"
    echo "  $0 /home/antoine/Project\ CEA/logo.png"
    echo ""
echo "Your favicon can be:"
echo "  - PNG or ICO format"
echo "  - Any size (will be resized automatically)"
echo "  - Square aspect ratio recommended"
echo "  - Larger images (like 112x112) will be used for high-resolution displays"
echo ""
    exit 1
fi

# Check if custom favicon exists
if [ ! -f "$CUSTOM_FAVICON" ]; then
    echo "Error: Custom favicon not found: $CUSTOM_FAVICON"
    exit 1
fi

# Grafana favicon locations
GRAFANA_PUBLIC="/usr/share/grafana/public"
FAVICON_DIR="$GRAFANA_PUBLIC/img"
FAVICON_PATH="$FAVICON_DIR/fav32.png"  # Grafana uses fav32.png, not favicon.png

# Check if Grafana is installed
if [ ! -d "$GRAFANA_PUBLIC" ]; then
    echo "Error: Grafana public directory not found: $GRAFANA_PUBLIC"
    echo "Make sure Grafana is installed."
    exit 1
fi

# Create backup directory
BACKUP_DIR="/tmp/grafana_favicon_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "Step 1: Backing up existing favicon..."
if [ -f "$FAVICON_PATH" ]; then
    sudo cp "$FAVICON_PATH" "$BACKUP_DIR/fav32.png"
    echo "  ✓ Backed up to: $BACKUP_DIR/fav32.png"
else
    echo "  ⚠ No existing favicon found (this is okay)"
fi

# Also backup other favicon locations
if [ -f "$FAVICON_DIR/favicon.png" ]; then
    sudo cp "$FAVICON_DIR/favicon.png" "$BACKUP_DIR/favicon.png"
    echo "  ✓ Backed up favicon.png"
fi
if [ -f "$FAVICON_DIR/favicon.ico" ]; then
    sudo cp "$FAVICON_DIR/favicon.ico" "$BACKUP_DIR/favicon.ico"
    echo "  ✓ Backed up favicon.ico"
fi

echo ""
echo "Step 2: Processing custom favicon (preserving full resolution)..."

# Get file extension
EXT="${CUSTOM_FAVICON##*.}"
EXT_LOWER=$(echo "$EXT" | tr '[:upper:]' '[:lower:]')

# Check if ImageMagick is available for resizing
HAS_IMAGEMAGICK=false
if command -v convert >/dev/null 2>&1; then
    HAS_IMAGEMAGICK=true
fi

# Get image dimensions
if [ "$HAS_IMAGEMAGICK" = true ]; then
    IMG_SIZE=$(identify -format "%wx%h" "$CUSTOM_FAVICON" 2>/dev/null)
    echo "  Image size: $IMG_SIZE"
fi

# Copy full resolution PNG (Grafana can use high-res favicons)
if [ "$EXT_LOWER" = "png" ]; then
    # Keep full resolution for fav32.png (Grafana's actual favicon file)
    sudo cp "$CUSTOM_FAVICON" "$FAVICON_PATH"
    echo "  ✓ Copied full resolution ($IMG_SIZE) to $FAVICON_PATH (fav32.png)"
    
    # Also create apple-touch-icon (typically 180x180, but we'll use your full res)
    if [ -f "$FAVICON_DIR/apple-touch-icon.png" ]; then
        sudo cp "$CUSTOM_FAVICON" "$FAVICON_DIR/apple-touch-icon.png"
        echo "  ✓ Updated apple-touch-icon.png (full resolution)"
    fi
    
    # Create ICO file with multiple sizes for browser compatibility
    if [ "$HAS_IMAGEMAGICK" = true ]; then
        # Create ICO with multiple embedded sizes (16, 32, 48, 64, 112)
        sudo convert "$CUSTOM_FAVICON" \
            \( -clone 0 -resize 16x16 \) \
            \( -clone 0 -resize 32x32 \) \
            \( -clone 0 -resize 48x48 \) \
            \( -clone 0 -resize 64x64 \) \
            \( -clone 0 -resize 112x112 \) \
            -delete 0 -alpha on -colors 256 "$FAVICON_DIR/favicon.ico" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "  ✓ Created favicon.ico with multiple sizes (16, 32, 48, 64, 112)"
        else
            # Fallback: simple ICO
            sudo convert "$CUSTOM_FAVICON" "$FAVICON_DIR/favicon.ico" 2>/dev/null
            echo "  ✓ Created favicon.ico (single size)"
        fi
    else
        # No ImageMagick, just copy as ICO
        sudo cp "$CUSTOM_FAVICON" "$FAVICON_DIR/favicon.ico"
        echo "  ✓ Copied as favicon.ico (full resolution)"
    fi
elif [ "$EXT_LOWER" = "ico" ]; then
    sudo cp "$CUSTOM_FAVICON" "$FAVICON_DIR/favicon.ico"
    echo "  ✓ Copied favicon.ico"
    
    # Convert ICO to PNG if ImageMagick available
    if [ "$HAS_IMAGEMAGICK" = true ]; then
        sudo convert "$CUSTOM_FAVICON" "$FAVICON_PATH"
        echo "  ✓ Converted to favicon.png"
    else
        echo "  ⚠ Warning: ImageMagick not found. PNG version not created."
        echo "  Install: sudo apt-get install imagemagick"
    fi
else
    echo "  Converting to PNG format..."
    if [ "$HAS_IMAGEMAGICK" = true ]; then
        # Convert and keep full resolution
        sudo convert "$CUSTOM_FAVICON" "$FAVICON_PATH"
        echo "  ✓ Converted to PNG (full resolution)"
    else
        echo "  ✗ Error: Cannot convert. Please provide a PNG or ICO file."
        echo "  Install ImageMagick: sudo apt-get install imagemagick"
        exit 1
    fi
fi

echo ""
echo "Step 3: Setting correct permissions..."
sudo chown root:root "$FAVICON_PATH" "$FAVICON_DIR/favicon.ico" 2>/dev/null
sudo chmod 644 "$FAVICON_PATH" "$FAVICON_DIR/favicon.ico" 2>/dev/null
if [ -f "$FAVICON_DIR/apple-touch-icon.png" ]; then
    sudo chown root:root "$FAVICON_DIR/apple-touch-icon.png"
    sudo chmod 644 "$FAVICON_DIR/apple-touch-icon.png"
fi
echo "  ✓ Permissions set"

echo ""
echo "Step 4: Restarting Grafana..."
sudo systemctl restart grafana-server
sleep 2

if systemctl is-active --quiet grafana-server; then
    echo "  ✓ Grafana restarted successfully"
else
    echo "  ✗ Warning: Grafana may not have restarted properly"
    echo "  Check status: sudo systemctl status grafana-server"
fi

echo ""
echo "=========================================="
echo "Done!"
echo "=========================================="
echo ""
echo "Your custom favicon should now be visible!"
echo ""
echo "To see the change:"
echo "  1. Clear your browser cache (Ctrl+Shift+Delete)"
echo "  2. Or use incognito/private mode"
echo "  3. Refresh Grafana (Ctrl+F5)"
echo ""
echo "Backup location: $BACKUP_DIR"
echo ""
echo "To restore original favicon:"
echo "  sudo cp $BACKUP_DIR/fav32.png $FAVICON_PATH"
echo "  sudo systemctl restart grafana-server"
echo ""

