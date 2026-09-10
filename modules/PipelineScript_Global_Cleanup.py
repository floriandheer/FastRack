#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlobalCleanup.py
Description: Combined utility for cleaning folders, system files, and temporary files
"""

import os
import sys
import time
import datetime
import argparse
import threading
import shutil
import logging
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from shared_window_icon import apply_category_icon

# Setup logging using shared utility
from shared_logging import get_logger, setup_logging as setup_shared_logging

# Get logger reference (configured in main())
logger = get_logger("global_cleanup")

class UnifiedCleaner:
    def __init__(self, root):
        self.root = root
        self.root.title("Unified File & Folder Cleaner")
        self.root.geometry("750x600")
        self.root.minsize(750, 500)
        
        # Configure main window
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        
        # Create header
        header_frame = tk.Frame(self.root, bg="#2c3e50", height=60)
        header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header_frame.grid_propagate(False)
        
        # Add title to header
        title_label = tk.Label(header_frame, text="Unified File & Folder Cleaner", 
                              font=("Arial", 16, "bold"), fg="white", bg="#2c3e50")
        title_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        # Create the notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        # ttk::Notebook ships a standard, cross-platform Tcl binding on its
        # "TNotebook" class that cycles tabs on any scroll over it - on Tk 9
        # that's driven by <TouchpadScroll> for a trackpad and by
        # <MouseWheel>/<Button-4/5> for an actual wheel, so a stray scroll
        # anywhere over the notebook would otherwise flip tabs unexpectedly.
        # Neutralize it outright since there's nothing else here to scroll.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<TouchpadScroll>"):
            self.root.bind_class("TNotebook", seq, lambda e: "break")

        # Create tabs
        self.create_folder_cleaner_tab()
        self.create_file_cleaner_tab()
        self.create_temp_cleaner_tab()
        
        # Create status bar
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, 
                                  relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=2, column=0, sticky="ew")
        
        # Initialize variables for folder cleaner
        self.scanning = False
        self.empty_folders = []
        self.nested_folders = []
        
    def create_folder_cleaner_tab(self):
        # Create tab for folder cleaner
        folder_frame = ttk.Frame(self.notebook)
        self.notebook.add(folder_frame, text="Folder Structure Cleaner")
        
        # Configure grid
        folder_frame.columnconfigure(0, weight=1)
        folder_frame.rowconfigure(2, weight=1)
        
        # Top control area
        control_frame = ttk.Frame(folder_frame)
        control_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        control_frame.columnconfigure(0, weight=1)
        
        # Directory selection
        dir_frame = ttk.Frame(control_frame)
        dir_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        dir_frame.columnconfigure(0, weight=1)
        
        self.folder_path = tk.StringVar()
        ttk.Label(dir_frame, text="Select Folder:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.folder_entry = ttk.Entry(dir_frame, textvariable=self.folder_path, width=50)
        self.folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        
        self.browse_folder_btn = ttk.Button(dir_frame, text="Browse", command=self.browse_folder)
        self.browse_folder_btn.grid(row=0, column=2)
        
        # Options section
        options_frame = ttk.LabelFrame(control_frame, text="Options")
        options_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        options_frame.columnconfigure(0, weight=1)
        
        # Checkboxes
        check_frame = ttk.Frame(options_frame)
        check_frame.grid(row=0, column=0, sticky="ew", pady=5)
        
        self.empty_var = tk.BooleanVar(value=True)
        self.empty_check = ttk.Checkbutton(check_frame, text="Find empty folders", 
                                         variable=self.empty_var)
        self.empty_check.grid(row=0, column=0, sticky="w", padx=5)
        
        self.nested_var = tk.BooleanVar(value=True)
        self.nested_check = ttk.Checkbutton(check_frame, text="Find unnecessarily nested folders", 
                                          variable=self.nested_var)
        self.nested_check.grid(row=0, column=1, sticky="w", padx=20)
        
        # Depth setting
        depth_frame = ttk.Frame(options_frame)
        depth_frame.grid(row=1, column=0, sticky="w", pady=5)
        
        ttk.Label(depth_frame, text="Minimum nesting depth:").grid(row=0, column=0, padx=5)
        self.depth_var = tk.IntVar(value=2)
        self.depth_spin = ttk.Spinbox(depth_frame, from_=1, to=10, width=3, textvariable=self.depth_var)
        self.depth_spin.grid(row=0, column=1, padx=5)
        
        # Action buttons
        btn_frame = ttk.Frame(control_frame)
        btn_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        
        self.scan_btn = ttk.Button(btn_frame, text="Scan Folders", 
                                 command=self.start_scan, width=15)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.clean_empty_btn = ttk.Button(btn_frame, text="Delete Empty Folders", 
                                        command=self.delete_empty_folders, 
                                        state=tk.DISABLED, width=20)
        self.clean_empty_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.unnest_btn = ttk.Button(btn_frame, text="Flatten Nested Folders", 
                                   command=self.move_nested_folders_up, 
                                   state=tk.DISABLED, width=20)
        self.unnest_btn.pack(side=tk.LEFT)
        
        # Results notebook
        self.folder_notebook = ttk.Notebook(folder_frame)
        self.folder_notebook.grid(row=2, column=0, sticky="nsew", pady=10)
        
        # Tab for empty folders
        self.empty_frame = ttk.Frame(self.folder_notebook)
        self.folder_notebook.add(self.empty_frame, text="Empty Folders")
        self.empty_frame.rowconfigure(0, weight=1)
        self.empty_frame.columnconfigure(0, weight=1)
        
        # Empty folders text area with scrollbar
        self.empty_scrollbar = ttk.Scrollbar(self.empty_frame)
        self.empty_scrollbar.grid(row=0, column=1, sticky="ns")
        
        self.empty_text = tk.Text(self.empty_frame, wrap=tk.WORD, 
                                yscrollcommand=self.empty_scrollbar.set)
        self.empty_text.grid(row=0, column=0, sticky="nsew")
        self.empty_scrollbar.config(command=self.empty_text.yview)
        
        # Tab for nested folders
        self.nested_frame = ttk.Frame(self.folder_notebook)
        self.folder_notebook.add(self.nested_frame, text="Nested Folders")
        self.nested_frame.rowconfigure(0, weight=1)
        self.nested_frame.columnconfigure(0, weight=1)
        
        # Nested folders text area with scrollbar
        self.nested_scrollbar = ttk.Scrollbar(self.nested_frame)
        self.nested_scrollbar.grid(row=0, column=1, sticky="ns")
        
        self.nested_text = tk.Text(self.nested_frame, wrap=tk.WORD, 
                                 yscrollcommand=self.nested_scrollbar.set)
        self.nested_text.grid(row=0, column=0, sticky="nsew")
        self.nested_scrollbar.config(command=self.nested_text.yview)
    
    def create_file_cleaner_tab(self):
        # Create tab for file cleaner
        file_frame = ttk.Frame(self.notebook)
        self.notebook.add(file_frame, text="System Files Cleaner")
        
        # Configure grid
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(1, weight=1)
        
        # Create form
        form_frame = ttk.LabelFrame(file_frame, text="Clean System Files")
        form_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        form_frame.columnconfigure(1, weight=1)
        
        # Folder selection
        ttk.Label(form_frame, text="Select Folder:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.file_folder_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.file_folder_var, width=50).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(form_frame, text="Browse", command=self.browse_file_folder).grid(row=0, column=2, padx=5, pady=10)
        
        # File types to clean
        ttk.Label(form_frame, text="Select file types to clean:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        
        types_frame = ttk.Frame(form_frame)
        types_frame.grid(row=2, column=0, columnspan=3, sticky="w", padx=30)
        
        self.ds_store_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(types_frame, text=".DS_Store files", variable=self.ds_store_var).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        
        self.thumbs_db_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(types_frame, text="Thumbs.db files", variable=self.thumbs_db_var).grid(row=0, column=1, sticky="w", padx=20, pady=5)
        
        # Custom file extensions
        ttk.Label(form_frame, text="Additional file extensions to clean (comma separated):").grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=10)
        self.custom_ext_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.custom_ext_var, width=50).grid(row=4, column=0, columnspan=3, sticky="ew", padx=30, pady=5)
        
        # Clean button
        clean_btn = ttk.Button(form_frame, text="Clean Files", command=self.clean_files)
        clean_btn.grid(row=5, column=0, sticky="w", padx=30, pady=20)
        clean_btn.config(width=20, padding=(10, 5))
        
        # Results frame
        results_frame = ttk.LabelFrame(file_frame, text="Results")
        results_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        
        # Text area for results with scrollbar
        self.file_scrollbar = ttk.Scrollbar(results_frame)
        self.file_scrollbar.grid(row=0, column=1, sticky="ns")
        
        self.file_results_text = tk.Text(results_frame, wrap=tk.WORD, 
                                       yscrollcommand=self.file_scrollbar.set)
        self.file_results_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.file_scrollbar.config(command=self.file_results_text.yview)

    def create_temp_cleaner_tab(self):
        # Create tab for temp file cleaner
        temp_frame = ttk.Frame(self.notebook)
        self.notebook.add(temp_frame, text="Temporary Files Cleaner")
        
        # Configure grid
        temp_frame.columnconfigure(0, weight=1)
        temp_frame.rowconfigure(1, weight=1)
        
        # Create form
        form_frame = ttk.LabelFrame(temp_frame, text="Clean Temporary Files")
        form_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        form_frame.columnconfigure(1, weight=1)
        
        # Temp directory selection
        ttk.Label(form_frame, text="Temp Directory:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        
        # Get default temp directory
        default_temp = os.environ.get("TEMP_DIR")
        if not default_temp:
            # Default to system temp directory
            default_temp = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")
            
        self.temp_dir_var = tk.StringVar(value=default_temp)
        ttk.Entry(form_frame, textvariable=self.temp_dir_var, width=50).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(form_frame, text="Browse", command=self.browse_temp_dir).grid(row=0, column=2, padx=5, pady=10)
        
        # Days to keep setting
        ttk.Label(form_frame, text="Keep files newer than (days):").grid(row=1, column=0, sticky="w", padx=10, pady=10)
        self.days_var = tk.IntVar(value=7)
        days_spin = ttk.Spinbox(form_frame, from_=1, to=365, width=5, textvariable=self.days_var)
        days_spin.grid(row=1, column=1, sticky="w", padx=5, pady=10)
        
        # Clean button
        clean_temp_btn = ttk.Button(form_frame, text="Clean Temp Files", command=self.clean_temp_files_ui)
        clean_temp_btn.grid(row=2, column=0, sticky="w", padx=30, pady=20)
        clean_temp_btn.config(width=20, padding=(10, 5))
        
        # Results frame
        results_frame = ttk.LabelFrame(temp_frame, text="Results")
        results_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        
        # Text area for results with scrollbar
        self.temp_scrollbar = ttk.Scrollbar(results_frame)
        self.temp_scrollbar.grid(row=0, column=1, sticky="ns")
        
        self.temp_results_text = tk.Text(results_frame, wrap=tk.WORD, 
                                       yscrollcommand=self.temp_scrollbar.set)
        self.temp_results_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.temp_scrollbar.config(command=self.temp_results_text.yview)
    
    # Folder Cleaner functions
    def browse_folder(self):
        directory = filedialog.askdirectory()
        if directory:
            self.folder_path.set(directory)
    
    def is_empty_dir(self, path):
        """Check if directory is empty (contains no files and no non-empty subdirectories)."""
        if not os.path.isdir(path):
            return False
            
        # Check immediate contents
        contents = os.listdir(path)
        if not contents:
            return True
            
        # Check if all contents are empty directories
        for item in contents:
            item_path = os.path.join(path, item)
            if os.path.isfile(item_path):
                return False
            if not self.is_empty_dir(item_path):
                return False
                
        return True
    
    def is_unnecessarily_nested(self, path, current_depth=1):
        """
        Check if a directory contains only a single subfolder that could be moved up.
        Returns a tuple (is_nested, nested_path, depth) where:
        - is_nested: True if the folder is unnecessarily nested
        - nested_path: Path to the innermost folder
        - depth: Depth of nesting
        """
        if not os.path.isdir(path):
            return False, path, 0
            
        contents = os.listdir(path)
        
        # Check if there's exactly one item and it's a directory
        if len(contents) == 1 and os.path.isdir(os.path.join(path, contents[0])):
            subdir_path = os.path.join(path, contents[0])
            # Recursively check the subdirectory
            is_nested, deepest_path, subdepth = self.is_unnecessarily_nested(subdir_path, current_depth + 1)
            
            if is_nested:
                # Pass through the deeper result
                return True, deepest_path, subdepth
            else:
                # This is the last folder in the chain
                return True, subdir_path, current_depth
        
        # Not a single-folder nesting case
        return False, path, 0
    
    def find_empty_folders(self, root_path):
        """Find all empty folders recursively."""
        empty_folders = []
        
        # First pass: collect all empty directories
        for dirpath, dirnames, filenames in os.walk(root_path, topdown=False):
            if self.is_empty_dir(dirpath):
                empty_folders.append(dirpath)
                
        return empty_folders
    
    def find_nested_folders(self, root_path, min_depth):
        """Find all unnecessarily nested folders."""
        nested_structures = []
        
        # Walk the directory tree
        for dirpath, dirnames, filenames in os.walk(root_path, topdown=True):
            # Skip paths that are already marked for unnesting
            skip = False
            for structure in nested_structures:
                if dirpath.startswith(structure[0] + os.sep):
                    skip = True
                    break
            if skip:
                continue
                
            is_nested, deepest_path, depth = self.is_unnecessarily_nested(dirpath)
            if is_nested and depth >= min_depth:
                # Store the root path of the nested structure and the deepest folder
                nested_structures.append((dirpath, deepest_path, depth))
                # Modify dirnames to skip already processed directories
                dirnames[:] = []
                
        return nested_structures
    
    def scan_thread(self):
        """Background thread for scanning folders."""
        try:
            root_path = self.folder_path.get()
            if not os.path.isdir(root_path):
                messagebox.showerror("Error", f"'{root_path}' is not a valid directory.")
                return
                
            self.status_var.set("Scanning folders...")
            min_depth = self.depth_var.get()
            
            # Reset results
            self.empty_text.delete(1.0, tk.END)
            self.nested_text.delete(1.0, tk.END)
            self.empty_folders = []
            self.nested_folders = []
            
            # Scan for empty folders if selected
            if self.empty_var.get():
                self.empty_text.insert(tk.END, f"Scanning {root_path} for empty folders...\n\n")
                self.root.update_idletasks()
                
                self.empty_folders = self.find_empty_folders(root_path)
                
                if not self.empty_folders:
                    self.empty_text.insert(tk.END, "No empty folders found.")
                else:
                    self.empty_text.insert(tk.END, f"Found {len(self.empty_folders)} empty folders:\n\n")
                    for folder in self.empty_folders:
                        self.empty_text.insert(tk.END, f"• {folder}\n")
                    self.clean_empty_btn.config(state=tk.NORMAL)
            
            # Scan for unnecessarily nested folders if selected
            if self.nested_var.get():
                self.nested_text.insert(tk.END, f"Scanning {root_path} for unnecessarily nested folders (min depth: {min_depth})...\n\n")
                self.root.update_idletasks()
                
                self.nested_folders = self.find_nested_folders(root_path, min_depth)
                
                if not self.nested_folders:
                    self.nested_text.insert(tk.END, "No unnecessarily nested folders found.")
                else:
                    self.nested_text.insert(tk.END, f"Found {len(self.nested_folders)} unnecessarily nested folder structures:\n\n")
                    for root_path, deepest_path, depth in self.nested_folders:
                        rel_path = os.path.relpath(deepest_path, root_path)
                        self.nested_text.insert(tk.END, f"• {root_path}\n")
                        self.nested_text.insert(tk.END, f"  ↳ Contains only: {rel_path} (depth: {depth})\n")
                        self.nested_text.insert(tk.END, f"  ↳ Will move: {deepest_path}\n")
                        self.nested_text.insert(tk.END, f"  ↳ To: {root_path}\n\n")
                    self.unnest_btn.config(state=tk.NORMAL)
            
            self.status_var.set("Scan complete")
            
            # Switch to the appropriate tab based on results
            if self.nested_var.get() and self.nested_folders:
                self.folder_notebook.select(1)  # Select nested folders tab
            elif self.empty_var.get() and self.empty_folders:
                self.folder_notebook.select(0)  # Select empty folders tab
            
        except Exception as e:
            if self.empty_var.get():
                self.empty_text.insert(tk.END, f"Error during scan: {str(e)}\n")
            if self.nested_var.get():
                self.nested_text.insert(tk.END, f"Error during scan: {str(e)}\n")
            self.status_var.set("Error occurred")
        finally:
            self.scanning = False
            self.scan_btn.config(state=tk.NORMAL)
    
    def start_scan(self):
        """Start scanning in a separate thread to keep UI responsive."""
        if not self.folder_path.get():
            messagebox.showinfo("Information", "Please select a directory first.")
            return
            
        if not self.scanning:
            self.scanning = True
            self.scan_btn.config(state=tk.DISABLED)
            self.clean_empty_btn.config(state=tk.DISABLED)
            self.unnest_btn.config(state=tk.DISABLED)
            threading.Thread(target=self.scan_thread, daemon=True).start()
    
    def delete_empty_folders(self):
        """Delete all the empty folders found during scan."""
        if not self.empty_folders:
            return
            
        if messagebox.askyesno("Confirm", f"Are you sure you want to delete {len(self.empty_folders)} empty folders?"):
            deleted_count = 0
            failed_count = 0
            self.empty_text.insert(tk.END, "\nDeleting empty folders:\n")
            
            # Sort in reverse to ensure children are deleted before parents
            for folder in sorted(self.empty_folders, reverse=True):
                try:
                    if os.path.exists(folder):  # Check if it still exists
                        os.rmdir(folder)
                        self.empty_text.insert(tk.END, f"✓ Deleted: {folder}\n")
                        deleted_count += 1
                    else:
                        self.empty_text.insert(tk.END, f"⚠ Already deleted: {folder}\n")
                except Exception as e:
                    self.empty_text.insert(tk.END, f"✗ Failed to delete: {folder} - {str(e)}\n")
                    failed_count += 1
            
            self.empty_text.insert(tk.END, f"\nSummary: {deleted_count} folders deleted, {failed_count} failed\n")
            self.status_var.set(f"Complete - {deleted_count} folders deleted")
            self.clean_empty_btn.config(state=tk.DISABLED)
            self.empty_folders = []
    
    def move_nested_folders_up(self):
        """Move unnecessarily nested folders up while preserving the root folder."""
        if not self.nested_folders:
            return
            
        if messagebox.askyesno("Confirm", f"Are you sure you want to flatten {len(self.nested_folders)} nested folder structures?"):
            moved_count = 0
            failed_count = 0
            self.nested_text.insert(tk.END, "\nFlattening nested folders within root folders:\n")
            
            for root_path, deepest_path, depth in self.nested_folders:
                try:
                    # Keep the root folder but move the deepest content directly under it
                    # Get the name of the deepest folder
                    deepest_name = os.path.basename(deepest_path)
                    
                    # Create the target path (directly under the root folder)
                    target_path = os.path.join(root_path, deepest_name)
                    
                    # Check if target already exists
                    if os.path.exists(target_path) and target_path != deepest_path:
                        # Generate a unique name
                        base, ext = os.path.splitext(deepest_name)
                        counter = 1
                        while os.path.exists(target_path):
                            target_path = os.path.join(root_path, f"{base}_{counter}{ext}")
                            counter += 1
                    
                    # Skip if the deepest folder is already directly under the root
                    if os.path.dirname(deepest_path) == root_path:
                        self.nested_text.insert(tk.END, f"Skipping: {deepest_path} - already directly under root\n\n")
                        continue
                    
                    # Move the deepest folder directly under the root folder
                    self.nested_text.insert(tk.END, f"Moving: {deepest_path}\n")
                    self.nested_text.insert(tk.END, f"To: {target_path}\n")
                    
                    # Perform the move
                    shutil.move(deepest_path, target_path)
                    moved_count += 1
                    
                    # Delete the now-empty intermediate directories
                    self.clean_empty_intermediate_dirs(root_path, deepest_path)
                    
                    self.nested_text.insert(tk.END, f"✓ Successfully moved: {deepest_path} -> {target_path}\n\n")
                except Exception as e:
                    self.nested_text.insert(tk.END, f"✗ Failed to move: {deepest_path} - {str(e)}\n\n")
                    failed_count += 1
            
            self.nested_text.insert(tk.END, f"\nSummary: {moved_count} nested structures moved, {failed_count} failed\n")
            self.status_var.set(f"Complete - {moved_count} nested structures moved")
            self.unnest_btn.config(state=tk.DISABLED)
            self.nested_folders = []
            
            # Suggest a rescan
            if moved_count > 0:
                self.nested_text.insert(tk.END, "\nYou may want to scan again to check for any remaining nested structures.\n")
    
    def clean_empty_intermediate_dirs(self, root_path, deepest_path):
        """Clean up empty intermediate directories after moving deepest folder."""
        current_dir = os.path.dirname(deepest_path)
        
        while current_dir != root_path and current_dir.startswith(root_path):
            try:
                if os.path.exists(current_dir) and not os.listdir(current_dir):
                    os.rmdir(current_dir)
                    self.nested_text.insert(tk.END, f"✓ Cleaned up empty dir: {current_dir}\n")
                else:
                    break  # Stop if directory is not empty
            except Exception as e:
                self.nested_text.insert(tk.END, f"✗ Failed to clean up: {current_dir} - {str(e)}\n")
                break
                
            current_dir = os.path.dirname(current_dir)
    
    # File Cleaner functions
    def browse_file_folder(self):
        directory = filedialog.askdirectory()
        if directory:
            self.file_folder_var.set(directory)
    
    def remove_files(self, folder_path, file_types, custom_extensions=None):
        """Remove specified file types recursively from folder_path."""
        removed_count = {file_type: 0 for file_type in file_types}
        custom_removed = 0
        
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_lower = file.lower()
                removed = False
                
                # Check for standard file types
                for file_type in file_types:
                    if file_lower == file_type:
                        file_path = os.path.join(root, file)
                        try:
                            os.remove(file_path)
                            removed_count[file_type] += 1
                            removed = True
                        except Exception as e:
                            self.file_results_text.insert(tk.END, f"Error removing {file_path}: {str(e)}\n")
                
                # Check for custom extensions
                if not removed and custom_extensions:
                    for ext in custom_extensions:
                        if file_lower.endswith(ext):
                            file_path = os.path.join(root, file)
                            try:
                                os.remove(file_path)
                                custom_removed += 1
                            except Exception as e:
                                self.file_results_text.insert(tk.END, f"Error removing {file_path}: {str(e)}\n")
                            break
        
        return removed_count, custom_removed
    
    def clean_files(self):
        """Clean system files based on user selection."""
        folder_path = self.file_folder_var.get()
        if not folder_path:
            messagebox.showerror("Error", "Please select a folder first.")
            return

        # Determine which file types to clean
        file_types = []
        if self.ds_store_var.get():
            file_types.append(".ds_store")
        if self.thumbs_db_var.get():
            file_types.append("thumbs.db")

        # Process custom extensions
        custom_extensions = []
        custom_ext_text = self.custom_ext_var.get().strip()
        if custom_ext_text:
            extensions = [ext.strip() for ext in custom_ext_text.split(',')]
            custom_extensions = ['.' + ext.lstrip('.').lower() for ext in extensions if ext]

        if not file_types and not custom_extensions:
            messagebox.showerror("Error", "Please select at least one file type to clean.")
            return

        # Clear the results text
        self.file_results_text.delete(1.0, tk.END)
        self.file_results_text.insert(tk.END, f"Cleaning files in: {folder_path}\n\n")
        
        # Set status
        self.status_var.set("Cleaning files...")
        self.root.update_idletasks()
        
        # Start cleaning in a separate thread
        def clean_thread():
            try:
                removed_count, custom_removed = self.remove_files(folder_path, file_types, custom_extensions)
                
                # Report results
                self.file_results_text.insert(tk.END, "Cleanup Complete:\n")
                for file_type, count in removed_count.items():
                    self.file_results_text.insert(tk.END, f"Removed {count} {file_type} files\n")
                
                if custom_extensions:
                    self.file_results_text.insert(tk.END, f"Removed {custom_removed} custom extension files\n")
                
                total_removed = sum(removed_count.values()) + custom_removed
                self.status_var.set(f"Complete - Removed {total_removed} files")
            except Exception as e:
                self.file_results_text.insert(tk.END, f"Error during cleanup: {str(e)}\n")
                self.status_var.set("Error occurred")
        
        threading.Thread(target=clean_thread, daemon=True).start()
    
    # Temp Files Cleaner functions
    def browse_temp_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.temp_dir_var.set(directory)
    
    def cleanup_temp_files(self, temp_dir, days_to_keep):
        """
        Clean up temporary files older than specified days.
        
        Args:
            temp_dir (str): Directory to clean up
            days_to_keep (int): Number of days to keep files
        
        Returns:
            tuple: (removed_count, error_count, empty_dirs_removed)
        """
        logger.info(f"Cleaning up temporary files in {temp_dir}")
        logger.info(f"Keeping files newer than {days_to_keep} days")
        
        if not os.path.exists(temp_dir):
            logger.error(f"Directory does not exist: {temp_dir}")
            return 0, 1, 0
        
        if not os.path.isdir(temp_dir):
            logger.error(f"Not a directory: {temp_dir}")
            return 0, 1, 0
        
        # Calculate cutoff time
        cutoff_time = time.time() - (days_to_keep * 86400)  # Convert days to seconds
        removed_count = 0
        error_count = 0
        
        # First, remove files
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    file_modified_time = os.path.getmtime(file_path)
                    if file_modified_time < cutoff_time:
                        logger.info(f"Removing file: {file_path}")
                        os.remove(file_path)
                        removed_count += 1
                        if removed_count % 100 == 0:
                            logger.info(f"Removed {removed_count} files so far...")
                except Exception as e:
                    logger.error(f"Error removing file {file_path}: {e}")
                    error_count += 1
        
        # Then, remove empty directories (bottom-up)
        empty_dirs_removed = 0
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                try:
                    if not os.listdir(dir_path):  # Check if directory is empty
                        logger.info(f"Removing empty directory: {dir_path}")
                        os.rmdir(dir_path)
                        empty_dirs_removed += 1
                except Exception as e:
                    logger.error(f"Error removing directory {dir_path}: {e}")
                    error_count += 1
        
        return removed_count, error_count, empty_dirs_removed
    
    def clean_temp_files_ui(self):
        """Clean temporary files based on user settings."""
        temp_dir = self.temp_dir_var.get()
        days = self.days_var.get()
        
        if not temp_dir:
            messagebox.showerror("Error", "Please specify a temporary directory.")
            return
        
        if not os.path.exists(temp_dir) or not os.path.isdir(temp_dir):
            messagebox.showerror("Error", f"'{temp_dir}' is not a valid directory.")
            return
        
        # Ask for confirmation
        if not messagebox.askyesno("Confirm", 
                                  f"This will remove files older than {days} days from:\n\n{temp_dir}\n\nDo you want to continue?",
                                  icon="warning"):
            return
        
        # Clear the results text
        self.temp_results_text.delete(1.0, tk.END)
        self.temp_results_text.insert(tk.END, f"Cleaning temporary files in: {temp_dir}\n")
        self.temp_results_text.insert(tk.END, f"Removing files older than {days} days\n\n")
        
        # Set status
        self.status_var.set("Cleaning temporary files...")
        self.root.update_idletasks()
        
        # Create a handler to redirect logs to the text widget
        class TextHandler(logging.Handler):
            def __init__(self, text_widget):
                logging.Handler.__init__(self)
                self.text_widget = text_widget
                
            def emit(self, record):
                msg = self.format(record)
                
                def append():
                    self.text_widget.insert(tk.END, msg + '\n')
                    self.text_widget.see(tk.END)
                
                # Schedule the update in the main thread
                self.text_widget.after(0, append)
        
        # Add text handler to logger
        text_handler = TextHandler(self.temp_results_text)
        text_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(text_handler)
        
        # Start cleaning in a separate thread
        def clean_thread():
            try:
                start_time = time.time()
                
                removed_count, error_count, empty_dirs_removed = self.cleanup_temp_files(temp_dir, days)
                
                elapsed_time = time.time() - start_time
                
                # Report summary
                self.temp_results_text.insert(tk.END, "\n" + "-"*50 + "\n")
                self.temp_results_text.insert(tk.END, f"Cleanup completed in {elapsed_time:.2f} seconds\n")
                self.temp_results_text.insert(tk.END, f"Removed {removed_count} files\n")
                self.temp_results_text.insert(tk.END, f"Removed {empty_dirs_removed} empty directories\n")
                self.temp_results_text.insert(tk.END, f"Encountered {error_count} errors\n")
                
                self.status_var.set(f"Complete - Removed {removed_count} temp files, {empty_dirs_removed} empty dirs")
            except Exception as e:
                self.temp_results_text.insert(tk.END, f"\nError during cleanup: {str(e)}\n")
                self.status_var.set("Error occurred")
            finally:
                # Remove the text handler
                logger.removeHandler(text_handler)
        
        threading.Thread(target=clean_thread, daemon=True).start()

def main():
    # Setup logging when the app actually runs (not at import time)
    setup_shared_logging("global_cleanup")

    # Check if we should run in command-line mode
    if len(sys.argv) > 1:
        # Parse command line arguments
        parser = argparse.ArgumentParser(description="Unified File and Folder Cleaner")
        subparsers = parser.add_subparsers(dest='command', help='Command to run')
        
        # Temp files cleanup command
        temp_parser = subparsers.add_parser('temp', help='Clean temporary files')
        temp_parser.add_argument("--days", type=int, default=7, help="Number of days to keep files (default: 7)")
        temp_parser.add_argument("--dir", help="Directory to clean (default: system temp)")
        temp_parser.add_argument("--auto-run", action="store_true", help="Run without confirmation")
        
        # System files cleanup command
        system_parser = subparsers.add_parser('system', help='Clean system files')
        system_parser.add_argument("--dir", required=True, help="Directory to clean")
        system_parser.add_argument("--ds-store", action="store_true", help="Clean .DS_Store files")
        system_parser.add_argument("--thumbs-db", action="store_true", help="Clean Thumbs.db files")
        system_parser.add_argument("--extensions", help="Additional extensions to clean (comma separated)")
        system_parser.add_argument("--auto-run", action="store_true", help="Run without confirmation")
        
        args = parser.parse_args()
        
        # Run appropriate command
        if args.command == 'temp':
            # Get temp directory 
            temp_dir = args.dir
            if not temp_dir:
                temp_dir = os.environ.get("TEMP_DIR")
                if not temp_dir:
                    # Default to system temp directory
                    temp_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")
            
            # Get confirmation if not auto-run
            if not args.auto_run:
                print(f"WARNING: This will remove files older than {args.days} days from {temp_dir}")
                response = input("Do you want to continue? (y/n): ").strip().lower()
                if response != 'y':
                    logger.info("Cleanup cancelled by user")
                    return 0
            
            # Create an instance without GUI
            cleaner = UnifiedCleaner(None)
            removed_count, error_count, empty_dirs_removed = cleaner.cleanup_temp_files(temp_dir, args.days)
            
            # Print summary
            print(f"Removed {removed_count} files")
            print(f"Removed {empty_dirs_removed} empty directories")
            print(f"Encountered {error_count} errors")
            
        elif args.command == 'system':
            # Prepare file types to clean
            file_types = []
            if args.ds_store:
                file_types.append(".ds_store")
            if args.thumbs_db:
                file_types.append("thumbs.db")
                
            # Prepare custom extensions
            custom_extensions = []
            if args.extensions:
                extensions = [ext.strip() for ext in args.extensions.split(',')]
                custom_extensions = ['.' + ext.lstrip('.').lower() for ext in extensions if ext]
            
            # Check if we have anything to clean
            if not file_types and not custom_extensions:
                print("Error: Please specify at least one file type to clean")
                return 1
            
            # Get confirmation if not auto-run
            if not args.auto_run:
                types_str = ", ".join(file_types + custom_extensions)
                print(f"WARNING: This will remove files of type {types_str} from {args.dir}")
                response = input("Do you want to continue? (y/n): ").strip().lower()
                if response != 'y':
                    logger.info("Cleanup cancelled by user")
                    return 0
            
            # Create an instance without GUI and clean files
            cleaner = UnifiedCleaner(None)
            removed_count, custom_removed = cleaner.remove_files(args.dir, file_types, custom_extensions)
            
            # Print summary
            for file_type, count in removed_count.items():
                print(f"Removed {count} {file_type} files")
            
            if custom_extensions:
                print(f"Removed {custom_removed} custom extension files")
            
        return 0
        
    else:
        # Run in GUI mode
        root = tk.Tk()
        apply_category_icon(root)
        app = UnifiedCleaner(root)
        root.mainloop()
        return 0

if __name__ == "__main__":
    sys.exit(main())