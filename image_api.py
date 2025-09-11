from fastapi import FastAPI, HTTPException, File, UploadFile
import requests
import json
import os
import uuid  # Thư viện để tạo tên file duy nhất
import shutil # Thư viện để ghi file

app = FastAPI()


COMFYUI_INPUT_DIR = r"D:\ComfyUI\ComfyUI_windows_portable\ComfyUI\input"
COMFYUI_URL = "http://127.0.0.1:8188"

LOAD_IMAGE_NODE_ID = "12" # Đây là ID ví dụ, bạn cần kiểm tra file JSON của mình

SAVE_IMAGE_NODE_ID = "23" 

# --- KẾT THÚC CẤU HÌNH ---


@app.post("/process_image")
async def process_image(file: UploadFile = File(...)):
    """
    Endpoint này nhận một file ảnh, xử lý nó qua ComfyUI,
    và gửi kết quả đến một backend khác.
    """
    try:
        # --- BƯỚC 1: LƯU FILE ẢNH TẢI LÊN VÀO THƯ MỤC INPUT CỦA COMFYUI ---

        # Kiểm tra xem thư mục input có tồn tại không
        if not os.path.isdir(COMFYUI_INPUT_DIR):
            raise HTTPException(
                status_code=500, 
                detail=f"Thư mục input của ComfyUI không tồn tại: {COMFYUI_INPUT_DIR}"
            )
            
        # Tạo một tên file duy nhất để tránh bị ghi đè
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(COMFYUI_INPUT_DIR, unique_filename)

        # Ghi file ảnh vào đĩa
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # --- BƯỚC 2: TẢI VÀ CHỈNH SỬA WORKFLOW ---
        
        # Tải workflow từ file JSON cho mỗi yêu cầu
        with open("ghibli_after_upscale.json", "r", encoding="utf-8") as f:
            workflow = json.load(f)

        # Cập nhật node LoadImage (ID ví dụ là "4") với tên file vừa lưu
        # Workflow sẽ tự động tìm file này trong thư mục /input của nó
        workflow[LOAD_IMAGE_NODE_ID]["inputs"]["image"] = unique_filename

        # --- BƯỚC 3: GỬI WORKFLOW ĐẾN COMFYUI VÀ CHỜ KẾT QUẢ ---
        
        # Gửi workflow đến ComfyUI API
        r = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail="Gửi workflow đến ComfyUI thất bại")
        
        prompt_id = r.json()["prompt_id"]

        # Chờ cho đến khi có kết quả, thử lại tối đa 5 lần nếu gặp lỗi kết nối hoặc chưa có kết quả
        max_retries = 30
        for attempt in range(max_retries):
            try:
                history_response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
                if history_response.status_code != 200:
                    raise HTTPException(status_code=500, detail="Lấy lịch sử từ ComfyUI thất bại")
                history = history_response.json()
                if prompt_id in history:
                    # Kiểm tra outputs có dữ liệu ảnh chưa
                    outputs = history[prompt_id].get("outputs", {})
                    output_node = outputs.get(SAVE_IMAGE_NODE_ID)
                    if output_node and "images" in output_node and output_node["images"]:
                        break
                # Nếu chưa có kết quả, chờ 2 giây rồi thử lại
                import time
                time.sleep(1)
            except Exception as ex:
                if attempt == max_retries - 1:
                    print(f"Lỗi khi lấy lịch sử ComfyUI: {ex}")
                    raise HTTPException(status_code=500, detail=f"Lấy lịch sử từ ComfyUI thất bại sau {max_retries} lần thử: {ex}")
                else:
                    print(f"Thử lại lần {attempt+1} do lỗi: {ex}")
        else:
            raise HTTPException(status_code=500, detail="Không lấy được kết quả từ ComfyUI sau nhiều lần thử hoặc ảnh chưa được tạo.")
        outputs = history[prompt_id]["outputs"]

        # --- BƯỚC 4: THU THẬP KẾT QUẢ VÀ GỬI ĐẾN BACKEND KHÁC ---

        # Thu thập đường dẫn file ảnh từ node SaveImage (ID ví dụ là "28")
        image_paths = []
        output_node = outputs.get(SAVE_IMAGE_NODE_ID)
        
        if output_node and "images" in output_node:
            for img in output_node["images"]:
                # ComfyUI trả về đường dẫn tương đối từ thư mục output của nó
                # Chúng ta cần ghép nó với đường dẫn gốc của ComfyUI để có đường dẫn tuyệt đối
                # Giả định thư mục ComfyUI là cha của thư mục input
                comfyui_base_dir = os.path.dirname(COMFYUI_INPUT_DIR)
                output_dir_name = img.get("subfolder", "")
                file_name = img.get("filename")
                
                # Đường dẫn tuyệt đối đến file output
                abs_path = os.path.join(comfyui_base_dir, "output", output_dir_name, file_name)
                image_paths.append(abs_path)

        if not image_paths:
            raise HTTPException(status_code=404, detail="Không tìm thấy ảnh nào trong kết quả workflow.")

        # POST từng ảnh cho backend khác
        BACKEND_URL = "https://l7w8f857-5000.asse.devtunnels.ms/upload"  # Thay bằng địa chỉ backend thực tế
        upload_results = []
        for img_path in image_paths:
            if not os.path.exists(img_path):
                continue
            with open(img_path, "rb") as img_file:
                files = {"file": (os.path.basename(img_path), img_file, "image/png")}
                try:
                    # Sửa lỗi chính tả: requests.post thay vì pqdost
                    resp = requests.post(BACKEND_URL, files=files,data = {"ip":"https://597cptb5-2310.asse.devtunnels.ms/"})
                    upload_results.append({
                        "image_path": img_path,
                        "backend_status": resp.status_code,
                        "backend_response": resp.text
                    })
                except Exception as ex:
                    upload_results.append({
                        "image_path": img_path,
                        "backend_status": "error",
                        "backend_response": str(ex)
                    })

        return {
            "status": "success",
            "processed_images": image_paths,
            "backend_uploads": upload_results
        }

    except Exception as e:
        # Ghi lại lỗi để dễ dàng debug
        print(f"Đã xảy ra lỗi: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=2310)