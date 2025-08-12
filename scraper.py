import json
import asyncio
import random
from playwright.async_api import async_playwright, TimeoutError
import requests
import logging
import argparse

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def login(page, credentials):
    """
    Logs into the website using a multi-step Okta process with human-like delays.
    """
    logging.info("Attempting to log in.")
    try:
        await page.goto("https://dorseywright.nasdaq.com/login", wait_until='networkidle')
        await asyncio.sleep(random.uniform(1, 3))

        # Step 1: Enter username and click Next
        logging.info("Entering username.")
        await page.fill('input[name="identifier"]', credentials['username'])
        await asyncio.sleep(random.uniform(0.5, 1.5))
        await page.click('input[type="submit"]')

        # Wait for the password field to appear
        logging.info("Waiting for password field.")
        password_selector = 'input[type="password"]'
        await page.wait_for_selector(password_selector, timeout=15000)
        await asyncio.sleep(random.uniform(1, 3))

        # Step 2: Enter password and click Sign In
        logging.info("Entering password.")
        await page.fill(password_selector, credentials['password'])
        await asyncio.sleep(random.uniform(0.5, 1.5))
        await page.click('input[type="submit"]')

        # Wait for navigation to complete
        await page.wait_for_load_state('networkidle', timeout=30000)

        await page.wait_for_selector("#shell-breadcrumbs-wrapper", timeout=15000)
        logging.info("Login successful.")
        return True
    except TimeoutError:
        logging.error("Timeout during login process. One of the steps may have failed.")
        return False
    except Exception as e:
        logging.error(f"An error occurred during login: {e}")
        return False

async def main(username, password):
    """
    The main function that orchestrates the scraping process.
    """
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        logging.error("The 'config.json' file was not found.")
        return
    except json.JSONDecodeError:
        logging.error("Error decoding 'config.json'.")
        return

    credentials = {"username": username, "password": password}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1920, 'height': 1080})

        if not await login(page, credentials):
            await browser.close()
            logging.info("Exiting script due to login failure.")
            return

        for task in config.get('tasks', []):
            url = task.get('url')
            webhook_url = task.get('webhook_url')
            data_points = task.get('data_points', [])

            if not url or not webhook_url:
                logging.warning("Skipping a task due to missing 'url' or 'webhook_url'.")
                continue

            try:
                logging.info(f"Navigating to {url}")
                await asyncio.sleep(random.uniform(2, 5))
                await page.goto(url, wait_until='networkidle')

                actions = task.get('actions', [])
                if actions:
                    logging.info(f"Performing {len(actions)} actions for task.")
                    for action in actions:
                        action_type = action.get('type')
                        selector = action.get('selector')

                        if not action_type or not selector:
                            logging.warning("Skipping action due to missing 'type' or 'selector'.")
                            continue

                        logging.info(f"Performing action: {action_type} on selector: {selector}")
                        await asyncio.sleep(random.uniform(1, 2))

                        if action_type == 'click':
                            await page.click(selector)
                        elif action_type == 'select':
                            value = action.get('value')
                            if value:
                                await page.select_option(selector, value=value)
                            else:
                                logging.warning(f"Skipping select action due to missing 'value'.")

                    logging.info("Waiting for page to update after actions.")
                    await page.wait_for_load_state('networkidle')
                    await asyncio.sleep(random.uniform(2, 4))

                all_scraped_data = {}
                for data_point in data_points:
                    dp_type = data_point.get('type', 'single')
                    dp_name = data_point.get('name')

                    if dp_type == 'table':
                        strategy = data_point.get('strategy', 'simple')
                        table_selector = data_point.get('table_selector')
                        columns = data_point.get('columns', [])
                        filter_config = data_point.get('filter')

                        if not table_selector or not columns:
                            logging.warning(f"Skipping table scraping for '{dp_name}' due to missing 'table_selector' or 'columns'.")
                            continue

                        try:
                            logging.info(f"Scraping table '{dp_name}' with strategy '{strategy}' from {url} using selector '{table_selector}'")
                            table_element = await page.wait_for_selector(table_selector, timeout=15000)

                            table_data = []
                            if strategy == 'simple':
                                rows = await table_element.query_selector_all('tbody tr')
                                for row in rows:
                                    if await row.query_selector('th'):
                                        continue
                                    cells = await row.query_selector_all('td')
                                    if not cells or len(cells) < len(columns):
                                        continue
                                    row_data = {}
                                    for col_config in columns:
                                        col_name = col_config.get('name')
                                        col_index = col_config.get('index')
                                        if col_name is not None and col_index is not None and col_index < len(cells):
                                            cell_text = await cells[col_index].inner_text()
                                            row_data[col_name] = cell_text.strip()

                                    if row_data:
                                        if filter_config:
                                            filter_col = filter_config.get('column')
                                            filter_vals = filter_config.get('values', [])
                                            if filter_col and filter_vals and row_data.get(filter_col) not in filter_vals:
                                                continue

                                        if 'Current' in row_data:
                                            row_data['Current'] = row_data['Current'].split('(')[0].strip()
                                        table_data.append(row_data)

                            elif strategy == 'rowspan_sectors':
                                rows = await table_element.query_selector_all('tbody tr')
                                current_sector = "Unknown"
                                for row in rows:
                                    first_cell_in_row = await row.query_selector('td')
                                    if first_cell_in_row and await first_cell_in_row.get_attribute('rowspan'):
                                        sector_name_element = await first_cell_in_row.query_selector('h4')
                                        if sector_name_element:
                                            current_sector = await sector_name_element.inner_text()

                                    if not await row.query_selector('th'):
                                        cells = await row.query_selector_all('td')
                                        if len(cells) == len(columns):
                                            row_data = {'sector': current_sector}
                                            for col_config in columns:
                                                col_name = col_config.get('name')
                                                col_index = col_config.get('index')
                                                if col_name is not None and col_index < len(cells):
                                                    cell_text = await cells[col_index].inner_text()
                                                    row_data[col_name] = cell_text.strip()

                                            if row_data:
                                                if filter_config:
                                                    filter_col = filter_config.get('column')
                                                    filter_vals = filter_config.get('values', [])
                                                    if filter_col and filter_vals and row_data.get(filter_col) not in filter_vals:
                                                        continue

                                                if 'Current' in row_data:
                                                    row_data['Current'] = row_data['Current'].split('(')[0].strip()
                                                table_data.append(row_data)

                            all_scraped_data[dp_name] = table_data

                        except Exception as e:
                            logging.error(f"Could not scrape table '{dp_name}' from {url}: {e}")
                            all_scraped_data[dp_name] = None

                    elif dp_type == 'single':
                        selector = data_point.get('selector')
                        if not selector or not dp_name:
                            logging.warning(f"Skipping single data point scraping due to missing 'selector' or 'name'.")
                            continue

                        try:
                            logging.info(f"Scraping '{dp_name}' from {url} using selector '{selector}'")
                            element = await page.wait_for_selector(selector, timeout=10000)
                            value = await element.inner_text()
                            all_scraped_data[dp_name] = value.strip()
                        except Exception as e:
                            logging.error(f"Could not scrape '{dp_name}' from {url}: {e}")
                            all_scraped_data[dp_name] = None

                if all_scraped_data:
                    logging.info(f"Scraped data from {url}: {all_scraped_data}")

                    try:
                        # Send the list of rows directly, as n8n webhooks often expect an array
                        if len(all_scraped_data.values()) > 0:
                            payload = list(all_scraped_data.values())[0]
                            response = requests.post(webhook_url, json=payload)
                        else:
                            logging.warning("No data scraped, not sending webhook.")
                            response = None
                        response.raise_for_status()
                        logging.info(f"Successfully sent data to webhook: {webhook_url}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"Failed to send data to webhook {webhook_url}: {e}")

            except Exception as e:
                logging.error(f"An error occurred while processing the task for URL {url}: {e}")

        await browser.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Scrape data from Dorsey Wright website.")
    parser.add_argument("--username", required=True, help="Username for login.")
    parser.add_argument("--password", required=True, help="Password for login.")
    args = parser.parse_args()
    asyncio.run(main(args.username, args.password))
