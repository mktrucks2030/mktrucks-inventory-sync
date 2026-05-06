name: RC Inventory Sync

on:
  push:
    paths:
      - 'jobber*.xlsx'
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install pandas openpyxl requests

      - name: Generate RC inventory CSV
        env:
          GITHUB_TOKEN: ${{ secrets.GH_PAT }}
          MATRIXIFY_API_KEY: ${{ secrets.MATRIXIFY_API_KEY }}
        run: python rc_sync.py
