import requests
import json
from django.conf import settings

PINATA_HEADERS = {
    "pinata_api_key": settings.PINATA_API_KEY,
    "pinata_secret_api_key": settings.PINATA_SECRET_API_KEY,
}


def upload_file_to_ipfs(file_obj, file_name):
    """
    Upload a single file to IPFS via Pinata.
    file_obj: Django InMemoryUploadedFile
    file_name: string for naming the file on IPFS
    """
    url = f"{settings.PINATA_BASE_URL}/pinFileToIPFS"
    files = {"file": (file_name, file_obj)}
    response = requests.post(url, files=files, headers=PINATA_HEADERS)

    if response.status_code != 200:
        raise Exception(f"IPFS upload failed: {response.text}")

    cid = response.json()["IpfsHash"]
    return f"ipfs://{cid}"


def upload_json_to_ipfs(metadata):
    """
    Upload metadata JSON to IPFS via Pinata.
    """
    url = f"{settings.PINATA_BASE_URL}/pinJSONToIPFS"
    response = requests.post(url, json=metadata, headers=PINATA_HEADERS)

    if response.status_code != 200:
        raise Exception(f"Metadata upload failed: {response.text}")

    cid = response.json()["IpfsHash"]
    return f"ipfs://{cid}"


def handle_property_upload(files, form_data):
    """
    Main function:
    1. Uploads media/docs to IPFS
    2. Builds metadata JSON
    3. Uploads metadata JSON to IPFS
    4. Returns metadata CID
    """

    # 1. Upload media
    images_cids = []
    for img in files.getlist("images"):
        images_cids.append(upload_file_to_ipfs(img, img.name))

    video_cid = None
    if "video" in files:
        video_cid = upload_file_to_ipfs(files["video"], files["video"].name)

    # 2. Upload documents
    title_deed_cid = upload_file_to_ipfs(files["title_deed"], files["title_deed"].name) if "title_deed" in files else None
    tax_certificate_cid = upload_file_to_ipfs(files["tax_certificate"], files["tax_certificate"].name) if "tax_certificate" in files else None
    utility_bills_cid = upload_file_to_ipfs(files["utility_bills"], files["utility_bills"].name) if "utility_bills" in files else None
    kyc_doc_cid = upload_file_to_ipfs(files["kyc_doc"], files["kyc_doc"].name) if "kyc_doc" in files else None

    # 3. Build metadata JSON
    metadata = {
        "title": form_data.get("title"),
        "description": form_data.get("description"),
        "location": form_data.get("location"),
        "property_type": form_data.get("property_type"),
        "size": form_data.get("size"),
        "bedrooms": form_data.get("bedrooms"),
        "year_built": form_data.get("year_built"),
        "ownership_type": form_data.get("ownership_type"),
        "price": form_data.get("price"),
        "fractionalisation": form_data.get("fractionalisation") == "true",
        "let_agent_suggest": form_data.get("let_agent_suggest") == "true",
        "documents": {
            "title_deed": title_deed_cid,
            "tax_certificate": tax_certificate_cid,
            "utility_bills": utility_bills_cid,
            "kyc_doc": kyc_doc_cid,
        },
        "media": {
            "images": images_cids,
            "video": video_cid,
        },
    }

    # 4. Upload metadata JSON
    metadata_cid = upload_json_to_ipfs(metadata)
    return metadata_cid
