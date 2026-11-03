"""Excel / CSV 上传解析工具"""


async def parse_upload_sheets(request=None, sheet_names=None, skip_rows=0, data=None, filename=None):
    """解析 xlsx / xls / csv。

    - 不传 data：从 request（multipart 的 file 字段 / raw body）读取（旧行为，单文件上传用）。
    - 传 data + filename：直接解析给定字节（批量上传复用，避免重复读 request）。
    返回 (文件名, {sheet名: 行列表[dict]})；csv 返回 {"": 行列表}。
    sheet_names 为白名单（手册 10.2）；None 读取全部 sheet。
    skip_rows 为表头前标题行数（int 全体一致，或 dict 按 sheet 分别指定）。
    """
    if data is None:
        if request is None:
            raise ValueError("需要 request 或 data")
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" in ctype:
            form = await request.form()
            upload = form.get("file")
            if upload is None:
                raise ValueError("缺少 file 字段")
            filename = upload.filename or "upload"
            data = await upload.read()
        else:
            filename = "raw.csv"
            data = await request.body()
    return _bytes_to_sheets(data, filename, sheet_names, skip_rows)


def _bytes_to_sheets(data, filename, sheet_names, skip_rows):
    import io
    import openpyxl

    skip_map = skip_rows if isinstance(skip_rows, dict) else {}
    default_skip = skip_rows if isinstance(skip_rows, int) else 1

    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        # read_only=True：流式读取，避免报表 sheet 声明整表尺寸（如 1048576 行）时解析极慢/超时
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        wanted = {str(s).strip() for s in sheet_names} if sheet_names else None
        out: dict = {}
        for ws in wb.worksheets:
            if wanted is not None and ws.title.strip() not in wanted:
                continue
            # 不信任 sheet XML 里的 <dimension> 声明：部分导出系统（SheetJS 系）会写入失实声明
            # （<dimension ref="A1"/>），read_only 模式以 dimension 作为迭代上界导致只读出首行、
            # 真实数据全部丢失。reset_dimensions() 是 openpyxl 针对该场景的官方处理。
            ws.reset_dimensions()
            n_skip = max(0, int(skip_map.get(ws.title.strip(), default_skip)))
            it = ws.iter_rows(values_only=True)
            for _ in range(n_skip):                  # 跳过表头前的标题行
                next(it, None)
            header = next(it, None)
            if not header:
                continue
            # 重复列名加 .N 后缀（与 pandas 一致，避免同行同名列相互覆盖丢值）
            seen: dict = {}
            norm_header = []
            for h in header:
                h = str(h).strip() if h is not None else ""
                if h in seen:
                    seen[h] += 1
                    norm_header.append(f"{h}.{seen[h]}")
                else:
                    seen[h] = 0
                    norm_header.append(h)
            header = norm_header
            rows = []
            try:
                for r in it:
                    if all(v is None or str(v).strip() == "" for v in r):
                        continue
                    rows.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
            except ValueError:
                # 规避 openpyxl 解析表尾 autoFilter 的已知缺陷
                pass
            out[ws.title] = rows
        wb.close()
        return filename, out

    text = data.decode("utf-8-sig", errors="replace")
    import csv
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        return filename, {"": []}
    header = [h.strip() for h in all_rows[0]]
    return filename, {"": [{header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
                           for row in all_rows[1:] if any(str(c).strip() for c in row)]}


async def parse_upload_raw(request=None, sheet_names=None, data=None, filename=None):
    """解析 xlsx，返回 (文件名, {sheet名: 原始行元组列表})（不设表头、不跳行，保留完整矩阵）。

    data + filename 优先（批量上传用），否则从 request 读取（旧行为）。
    """
    if data is None:
        if request is None:
            raise ValueError("需要 request 或 data")
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" in ctype:
            form = await request.form()
            upload = form.get("file")
            if upload is None:
                raise ValueError("缺少 file 字段")
            filename = upload.filename or "upload"
            data = await upload.read()
        else:
            filename = "raw.csv"
            data = await request.body()
    return _bytes_to_raw(data, filename, sheet_names)


def _bytes_to_raw(data, filename, sheet_names):
    import io
    import openpyxl

    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        wanted = {str(s).strip() for s in sheet_names} if sheet_names else None
        out: dict = {}
        for ws in wb.worksheets:
            if wanted is not None and ws.title.strip() not in wanted:
                continue
            ws.reset_dimensions()
            out[ws.title] = [tuple(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        return filename, out
    import csv
    text = data.decode("utf-8-sig", errors="replace")
    return filename, {"": [tuple(r) for r in csv.reader(io.StringIO(text))]}


async def parse_upload_multi_raw(request, sheet_names=None):
    """解析 multipart 请求中的 **多个** xlsx，返回 [(文件名, {sheet名: 原始行元组列表}), ...]。

    质检通报每日 5 个附件，一次多选上传比逐个传 5 次合理；同时兼容旧的单文件
    （字段名 file）调用。sheet 白名单对所有文件共用一份。
    """
    import io
    import openpyxl

    ctype = request.headers.get("content-type", "")
    uploads = []
    if "multipart/form-data" in ctype:
        form = await request.form()
        for up in form.getlist("files"):
            if up is not None:
                uploads.append(up)
        single = form.get("file")
        if single is not None and getattr(single, "filename", None):
            uploads.append(single)
    else:
        uploads.append(None)
    if not uploads:
        raise ValueError("缺少 file / files 字段")

    out: list = []
    for upload in uploads:
        if upload is None:
            filename, data = "raw.csv", await request.body()
        else:
            filename = upload.filename or "upload"
            data = await upload.read()
        if not data:
            continue
        if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            wanted = {str(s).strip() for s in sheet_names} if sheet_names else None
            sheets: dict = {}
            for ws in wb.worksheets:
                if wanted is not None and ws.title.strip() not in wanted:
                    continue
                ws.reset_dimensions()
                rows = []
                try:
                    for r in ws.iter_rows(values_only=True):
                        rows.append(tuple(r))
                except ValueError:
                    pass
                sheets[ws.title] = rows
            wb.close()
            out.append((filename, sheets))
        else:
            import csv
            text = data.decode("utf-8-sig", errors="replace")
            out.append((filename, {"": [tuple(r) for r in csv.reader(io.StringIO(text))]}))
    return out


async def parse_upload(request=None, data=None, filename=None):
    """从 request 或给定字节解析 xlsx / csv（全部 sheet 合并），返回 (文件名, 行列表[dict])"""
    if data is None:
        filename, sheets = await parse_upload_sheets(request)
    else:
        filename, sheets = await parse_upload_sheets(data=data, filename=filename)
    rows: list = []
    for rs in sheets.values():
        rows.extend(rs)
    return filename, rows
