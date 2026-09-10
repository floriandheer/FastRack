WooCommerce Order Monitor - Data Folder
========================================

This folder contains all configuration and data files for the WooCommerce Order Monitor.

Files:
------

config.json
    Your WooCommerce API credentials and monitor settings.
    Create this by copying config.json.example and filling in your details.

config.json.example
    Template configuration file with example values.
    Copy this to config.json and customize.

processed_orders.json
    Tracks which orders have been processed (auto-generated).
    Prevents downloading the same order multiple times.

woocommerce_monitor.log (in %LOCALAPPDATA%\PipelineManager\logs\)
    Activity log file (auto-generated).
    Contains details of all monitor operations.
    Located in the centralized FastRack logs folder.

Setup:
------

1. Copy config.json.example to config.json
2. Edit config.json with your WooCommerce credentials
3. Run the monitor script

All other files are automatically generated and managed by the monitor.
