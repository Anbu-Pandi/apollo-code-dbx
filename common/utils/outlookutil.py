MAPI_INBOX_FOLDER = 6


def require_outlook_client():
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "pywin32 is required for Outlook automation. Install pywin32 on this Windows host."
        ) from exc
    return win32com.client


def safe_str(value) -> str:
    return str(value or "").strip()


def outlook_namespace():
    client = require_outlook_client()
    application = client.Dispatch("Outlook.Application")
    namespace = application.GetNamespace("MAPI")
    try:
        namespace.GetDefaultFolder
    except AttributeError:
        return application.Session
    return namespace


def find_child_folder(parent, child_name: str):
    needle = child_name.strip().lower()
    for idx in range(1, parent.Folders.Count + 1):
        folder = parent.Folders.Item(idx)
        if safe_str(folder.Name).lower() == needle:
            return folder
    return None


def resolve_inbox_folder(folder_name: str):
    namespace = outlook_namespace()
    inbox = namespace.GetDefaultFolder(MAPI_INBOX_FOLDER)
    normalized = safe_str(folder_name)
    if not normalized or normalized.lower() == "inbox":
        return inbox

    current = inbox
    for section in [part for part in normalized.split("/") if part.strip()]:
        current = find_child_folder(current, section)
        if current is None:
            raise ValueError(f"Outlook folder not found: Inbox/{normalized}")
    return current


def walk_outlook_folders(folder, prefix: str):
    current_path = f"{prefix}/{safe_str(folder.Name)}" if prefix else safe_str(folder.Name)
    yield folder, current_path
    for idx in range(1, folder.Folders.Count + 1):
        child = folder.Folders.Item(idx)
        yield from walk_outlook_folders(child, current_path)


def list_outlook_folders(
    folder_names,
    *,
    max_depth: int = 0,
    include_item_counts: bool = True,
) -> list[dict]:
    if isinstance(folder_names, str):
        roots = [part.strip() for part in folder_names.split(",") if part.strip()]
    else:
        roots = [safe_str(folder_name) for folder_name in (folder_names or []) if safe_str(folder_name)]
    if not roots:
        roots = ["Inbox"]

    rows: list[dict] = []
    for root_name in roots:
        try:
            root = resolve_inbox_folder(root_name)
            root_path = f"Inbox/{root_name}" if root_name.lower() != "inbox" else "Inbox"
            _append_folder_rows(
                rows=rows,
                folder=root,
                folder_path=root_path,
                depth=0,
                max_depth=max_depth,
                include_item_counts=include_item_counts,
            )
        except Exception as exc:
            rows.append(
                {
                    "folder_path": f"Inbox/{root_name}",
                    "folder_name": root_name,
                    "relative_path": root_name,
                    "depth": 0,
                    "child_count": None,
                    "item_count": None,
                    "unread_count": None,
                    "error": str(exc),
                }
            )
    return rows


def _append_folder_rows(
    *,
    rows: list[dict],
    folder,
    folder_path: str,
    depth: int,
    max_depth: int,
    include_item_counts: bool,
) -> None:
    item_count = None
    unread_count = None
    error = ""
    if include_item_counts:
        try:
            item_count = int(getattr(getattr(folder, "Items", None), "Count", 0) or 0)
            unread_count = int(getattr(folder, "UnReadItemCount", 0) or 0)
        except Exception as exc:
            error = str(exc)

    child_count = int(getattr(getattr(folder, "Folders", None), "Count", 0) or 0)
    rows.append(
        {
            "folder_path": folder_path,
            "folder_name": safe_str(folder.Name),
            "relative_path": folder_path.removeprefix("Inbox/"),
            "depth": depth,
            "child_count": child_count,
            "item_count": item_count,
            "unread_count": unread_count,
            "error": error,
        }
    )
    if max_depth and depth >= max_depth:
        return
    for idx in range(1, folder.Folders.Count + 1):
        child = folder.Folders.Item(idx)
        child_name = safe_str(child.Name)
        _append_folder_rows(
            rows=rows,
            folder=child,
            folder_path=f"{folder_path}/{child_name}",
            depth=depth + 1,
            max_depth=max_depth,
            include_item_counts=include_item_counts,
        )
