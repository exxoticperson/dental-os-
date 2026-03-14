from __future__ import annotations

import mimetypes
import os

from googleapiclient.http import MediaFileUpload

from dental_os.config import AppConfig
from dental_os.services.google import GoogleClients


class DriveService:
    def __init__(self, config: AppConfig, google: GoogleClients) -> None:
        self.config = config
        self.drive = google.drive_api
        self._root_folder_id: str | None = config.google_drive_root_folder_id

    def ensure_root_folder(self) -> str:
        if self._root_folder_id:
            return self._root_folder_id
        self._root_folder_id = self._get_or_create_folder(self.config.google_drive_root_folder_name)
        return self._root_folder_id

    def ensure_case_folder(self, subject: str, case_id: str, patient_name: str) -> str:
        root_id = self.ensure_root_folder()
        subject_folder = self._get_or_create_folder(subject or "Unsorted", parent_id=root_id)
        case_folder_name = f"{case_id}_{patient_name}".replace("/", "-").strip()
        return self._get_or_create_folder(case_folder_name, parent_id=subject_folder)

    def upload_patient_photo(self, file_path: str, subject: str, case_id: str, patient_name: str) -> str:
        folder_id = self.ensure_case_folder(subject, case_id, patient_name)
        mime_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
        metadata = {"name": os.path.basename(file_path), "parents": [folder_id]}
        created = self.drive.files().create(
            body=metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()
        return created["webViewLink"]

    def _get_or_create_folder(self, name: str, parent_id: str | None = None) -> str:
        safe_name = name.replace("'", "\\'")
        query_parts = [
            "mimeType='application/vnd.google-apps.folder'",
            "trashed=false",
            f"name='{safe_name}'",
        ]
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")
        response = self.drive.files().list(
            q=" and ".join(query_parts),
            spaces="drive",
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=1,
        ).execute()
        files = response.get("files", [])
        if files:
            return files[0]["id"]
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            body["parents"] = [parent_id]
        created = self.drive.files().create(body=body, fields="id", supportsAllDrives=True).execute()
        return created["id"]
