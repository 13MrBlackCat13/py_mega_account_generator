"""Main file for the project, handles the arguments and calls the other files."""

import asyncio
import argparse
import os
import sys
from typing import Tuple
import pyppeteer
import json
import re
import shutil
from services.alive import keepalive
from services.upload import upload_file
from services.extract import extract_credentials
from utilities.fs import (
	Config,
	concrete_read_config,
	read_config,
	write_config,
	write_default_config,
	save_credentials,
)
from utilities.web import (
	finish_form,
	generate_mail,
	type_name,
	type_password,
	initial_setup,
	mail_login,
	get_mail,
)
from utilities.etc import (
	Credentials,
	p_print,
	clear_console,
	Colours,
	clear_tmp,
	reinstall_tenacity,
	check_for_updates,
	delete_default,
)

# Spooky import to check if the correct version of tenacity is installed.
if sys.version_info.major == 3 and sys.version_info.minor <= 11:
	try:
		pass
	except AttributeError:
		reinstall_tenacity()

default_installs = [
	"C:/Program Files/Google/Chrome/Application/chrome.exe",
	"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
	"C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
	"C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe",
	"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]
args = [
	"--no-sandbox",
	"--disable-setuid-sandbox",
	"--disable-infobars",
	"--window-position=0,0",
	"--ignore-certificate-errors",
	"--ignore-certificate-errors-spki-list",
	'--user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"',
]

parser = argparse.ArgumentParser()
parser.add_argument(
	"-ka",
	"--keepalive",
	required=False,
	action="store_true",
	help="Logs into the accounts to keep them alive.",
)
parser.add_argument(
	"-e",
	"--extract",
	required=False,
	action="store_true",
	help="Extracts the credentials to a file.",
)
parser.add_argument(
	"-v",
	"--verbose",
	required=False,
	action="store_true",
	help="Shows storage left while using keepalive function.",
)
parser.add_argument(
	"-f", "--file", required=False, help="Uploads a file to the account."
)
parser.add_argument(
	"-p",
	"--public",
	required=False,
	action="store_true",
	help="Generates a public link to the uploaded file, use with -f",
)
parser.add_argument(
	"-l",
	"--loop",
	required=False,
	help="Loops the program for a specified amount of times.",
	type=int,
)
parser.add_argument(
	"-c",
	"--convert",
	required=False,
	action="store_true",
	help="Converts accounts.txt from credentials folder to individual JSON files",
)

console_args = parser.parse_args()


def setup() -> Tuple[str, Config]:
	"""Sets up the configs so everything runs smoothly."""

	executable_path = ""
	config = read_config()

	if config is None:
		write_default_config()
		config = concrete_read_config()
	else:
		executable_path = config.executablePath

	# If no Chromium based browser is found, ask the user for the path to one.
	if not executable_path:
		p_print(
			"Failed to find a Chromium based browser. Please make sure you have one installed.",
			Colours.FAIL,
		)
		executable_path = input(
			"Please enter the path to a Chromium based browser's executable: "
		)
		if os.path.exists(executable_path):
			p_print("Found executable!", Colours.OKGREEN)
			write_config("executablePath", executable_path, config)
		else:
			p_print("Failed to find executable!", Colours.FAIL)
			sys.exit(1)

	return executable_path, config


def archive_folder(folder_path: str) -> str:
	"""
	Archives a folder and returns path to the archive
	"""
	if not os.path.isdir(folder_path):
		return folder_path

	archive_path = folder_path.rstrip("/\\") + ".zip"
	shutil.make_archive(folder_path, "zip", folder_path)
	return archive_path


def convert_accounts():
	"""
	Converts accounts.txt from credentials folder to individual JSON files
	"""

	def parse_account(account_text):
		email_match = re.search(r"Email: (.+)", account_text)
		email_pass_match = re.search(r"Email Password: (.+)", account_text)
		mega_pass_match = re.search(r"Mega Password: (.+)", account_text)

		if not all([email_match, email_pass_match, mega_pass_match]):
			return None

		return {
			"email": email_match.group(1),
			"emailPassword": email_pass_match.group(1),
			"password": mega_pass_match.group(1),
		}

	base_dir = os.path.dirname(os.path.abspath(__file__))
	input_path = os.path.join(base_dir, "credentials", "accounts.txt")
	output_dir = os.path.dirname(input_path)

	if not os.path.exists(input_path):
		p_print("accounts.txt not found in credentials folder!", Colours.FAIL)
		return

	with open(input_path, "r", encoding="utf-8") as f:
		content = f.read()

	accounts = content.split("-------------------")
	converted = 0

	for account in accounts:
		if not account.strip():
			continue

		account_data = parse_account(account.strip())
		if account_data:
			email = account_data["email"]
			filename = f"{email.split('@')[0]}@{email.split('@')[1].split('.')[0]}.json"
			output_path = os.path.join(output_dir, filename)

			with open(output_path, "w", encoding="utf-8") as f:
				json.dump(account_data, f, indent=2)
			converted += 1

	p_print(
		f"Successfully converted {converted} accounts to JSON format.", Colours.OKGREEN
	)
	p_print(f"JSON files saved in: {output_dir}", Colours.OKCYAN)


def loop_registrations(loop_count: int, executable_path: str, config: Config):
	"""Registers accounts in a loop."""
	for _ in range(loop_count):
		p_print(f"Loop {_ + 1}/{loop_count}", Colours.OKGREEN)
		clear_tmp()

		credentials = asyncio.run(generate_mail())
		asyncio.run(register(credentials, executable_path, config))


async def register(credentials: Credentials, executable_path: str, config: Config):
	"""Registers and verifies mega.nz account."""
	browser = await pyppeteer.launch(
		{
			"headless": True,
			"ignoreHTTPSErrors": True,
			"userDataDir": f"{os.getcwd()}/tmp",
			"args": args,
			"executablePath": executable_path,
			"autoClose": False,  # We run into runtime errors if we use autoClose
			"ignoreDefaultArgs": ["--enable-automation", "--disable-extensions"],
		}
	)

	context = await browser.createIncognitoBrowserContext()
	page = await context.newPage()

	await type_name(page, credentials)
	await type_password(page, credentials)
	await finish_form(page, credentials)
	mail = await mail_login(credentials)

	await asyncio.sleep(1.5)
	message = await get_mail(mail)

	await initial_setup(context, message, credentials)
	await asyncio.sleep(0.5)
	await browser.close()

	p_print("Verified account.", Colours.OKGREEN)
	p_print(
		f"Email: {credentials.email}\nPassword: {credentials.password}",
		Colours.OKCYAN,
	)

	delete_default(credentials)
	save_credentials(credentials, config.accountFormat)

	if console_args.file is not None:
		file_size = os.path.getsize(console_args.file)
		if os.path.exists(console_args.file) and 0 < file_size < 2e10:
			if file_size >= 5e9:
				p_print(
					"File is larger than 5GB, mega.nz limits traffic to 5GB per IP.",
					Colours.WARNING,
				)
			upload_file(console_args.public, console_args.file, credentials)
		else:
			p_print("File not found.", Colours.FAIL)
	if console_args.loop is None or console_args.loop <= 1:
		sys.exit(0)


if __name__ == "__main__":
	clear_console()
	check_for_updates()

	if console_args.convert:
		convert_accounts()
		sys.exit(0)

	executable_path, config = setup()
	if not executable_path:
		p_print("Failed while setting up!", Colours.FAIL)
		sys.exit(1)

	if console_args.extract:
		extract_credentials(config.accountFormat)
	elif console_args.keepalive:
		keepalive(console_args.verbose)
	elif console_args.loop is not None and console_args.loop > 1:
		loop_registrations(console_args.loop, executable_path, config)
	else:
		clear_tmp()
		credentials = asyncio.run(generate_mail())

		if console_args.file:
			if os.path.isdir(console_args.file):
				console_args.file = archive_folder(console_args.file)
				p_print(f"Folder archived to: {console_args.file}", Colours.OKCYAN)

		asyncio.run(register(credentials, executable_path, config))
