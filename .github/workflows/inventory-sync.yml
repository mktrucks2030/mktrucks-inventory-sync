name: MK Trucks Inventory Sync

on:
  schedule:
    - cron: '0 6,18 * * *'
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 120

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install google-auth==2.29.0 google-auth-oauthlib==1.2.0 google-api-python-client==2.126.0 pandas==2.2.2 openpyxl==3.1.2 requests==2.31.0

      - name: Create scripts dir and run sync
        env:
          GMAIL_CLIENT_ID:     ${{ secrets.GMAIL_CLIENT_ID }}
          GMAIL_CLIENT_SECRET: ${{ secrets.GMAIL_CLIENT_SECRET }}
          GMAIL_REFRESH_TOKEN: ${{ secrets.GMAIL_REFRESH_TOKEN }}
          MATRIXIFY_API_KEY:   ${{ secrets.MATRIXIFY_API_KEY }}
          SHOPIFY_STORE:       ${{ secrets.SHOPIFY_STORE }}
        run: python sync.py
