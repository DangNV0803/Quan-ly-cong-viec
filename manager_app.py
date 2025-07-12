import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, timezone
from collections import defaultdict
import requests
from zoneinfo import ZoneInfo
import re
import unicodedata
import time

# --- Page Configuration ---
st.set_page_config(
    layout="wide",
    page_title="Trang Quản lý",
    page_icon="👨‍💼"
)

# --- Supabase Connection ---
@st.cache_resource
def init_supabase_auth_client() -> Client:
    """Initializes a client for authentication using the anon key."""
    try:
        url = st.secrets["supabase_new"]["url"]
        key = st.secrets["supabase_new"]["anon_key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Lỗi cấu hình Supabase (Auth). Chi tiết: {e}")
        st.stop()

@st.cache_resource
def init_supabase_admin_client(project_name: str) -> Client:
    """Initializes a client for admin actions using the service key."""
    try:
        url = st.secrets[project_name]["url"]
        key = st.secrets[project_name]["service_key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Lỗi cấu hình Supabase cho '{project_name}'. Chi tiết: {e}")
        st.stop()

supabase_auth = init_supabase_auth_client()
supabase_new = init_supabase_admin_client("supabase_new")
supabase_old = init_supabase_admin_client("supabase_old")


# --- DATA FETCHING & UPDATING FUNCTIONS ---

@st.cache_data(ttl=600)
def fetch_old_projects(_client: Client):
    """Fetches projects from the old database."""
    try:
        response = _client.table('quotations').select('quotation_no, customer_name, project_type, status').execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"Lỗi khi lấy dữ liệu dự án từ hệ thống cũ: {e}")
        return None

@st.cache_data(ttl=300)
def fetch_all_profiles(_client: Client):
    """Fetches all user profiles from the new system."""
    try:
        response = _client.table('profiles').select('id, full_name, email, role, account_status').order('full_name').execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"Lỗi khi lấy danh sách nhân viên: {e}")
        return None
        
@st.cache_data(ttl=60)
def fetch_all_projects_new(_client: Client):
    """Fetches all projects from the new database."""
    try:
        response = _client.table('projects').select('*').order('created_at', desc=True).execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"Lỗi khi lấy danh sách dự án mới: {e}")
        return None


@st.cache_data(ttl=60)
def fetch_all_tasks_and_details(_client: Client):
    """Fetches all tasks and joins related data like project and profile names."""
    try:
        tasks_res = _client.table('tasks').select('*, projects(project_name, old_project_ref_id)').order('created_at', desc=True).execute()
        tasks = tasks_res.data if tasks_res.data else []
        profiles = fetch_all_profiles(_client)
        if profiles is None: profiles = []
        profile_map = {p['id']: p.get('full_name', 'N/A') for p in profiles}
        for task in tasks:
            task['assignee_name'] = profile_map.get(task.get('assigned_to'))
            task['creator_name'] = profile_map.get(task.get('created_by'))
            if task.get('projects'):
                task['project_name'] = task['projects']['project_name']
        return tasks
    except Exception as e:
        st.error(f"Lỗi khi lấy danh sách công việc: {e}")
        return None

@st.cache_data(ttl=30)
def fetch_comments(task_id: int):
    """Fetches comments for a specific task, joining with profile info."""
    try:
        response = supabase_new.table('comments').select('*, profiles(full_name, role), attachment_url').eq('task_id', task_id).order('created_at', desc=True).execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"Lỗi khi tải bình luận: {e}")
        return []

def get_deadline_color(due_date_str: str) -> str:
    """
    Trả về mã màu nền dựa trên thời gian còn lại đến hạn chót.
    - Đỏ: < 3 ngày hoặc quá hạn
    - Cam: 3-7 ngày
    - Vàng: 7-15 ngày
    - Xanh: > 15 ngày
    """
    if not due_date_str:
        return "#f5f5f5"  # Màu xám nhạt nếu không có deadline

    try:
        # Đặt múi giờ Việt Nam
        local_tz = ZoneInfo("Asia/Ho_Chi_Minh")
        
        # Chuyển đổi deadline và thời gian hiện tại sang cùng múi giờ
        due_date = datetime.fromisoformat(due_date_str).astimezone(local_tz)
        now = datetime.now(local_tz)
        
        time_remaining = due_date - now
        days_remaining = time_remaining.days

        if days_remaining < 3:
            return "#ffebee"  # Đỏ nhạt
        elif 3 <= days_remaining < 7:
            return "#fff3e0"  # Cam nhạt
        elif 7 <= days_remaining < 15:
            return "#fffde7"  # Vàng nhạt
        else:
            return "#e8f5e9"  # Xanh nhạt
            
    except (ValueError, TypeError):
        return "#f5f5f5"  # Trả về màu xám nếu có lỗi
    
def sanitize_filename(filename: str) -> str:
    """
    "Làm sạch" tên file: chuyển thành chữ không dấu, bỏ ký tự đặc biệt,
    thay thế khoảng trắng bằng gạch nối.
    """
    # Chuyển chuỗi unicode (có dấu) thành dạng gần nhất không dấu
    value = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
    # Loại bỏ các ký tự không phải là chữ, số, dấu gạch dưới, gạch nối, dấu chấm
    value = re.sub(r'[^\w\s.-]', '', value).strip()
    # Thay thế một hoặc nhiều khoảng trắng/gạch nối bằng một gạch nối duy nhất
    value = re.sub(r'[-\s]+', '-', value)
    return value

@st.cache_data(ttl=60)
def fetch_read_statuses(_supabase_client: Client, user_id: str):
    """Fetches all read statuses for the user, returns a dict of task_id -> UTC datetime."""
    try:
        response = _supabase_client.table('task_read_status').select('task_id, last_read_at').eq('user_id', user_id).execute()
        if response.data:
            # Luôn chuyển đổi sang UTC để so sánh nhất quán
            return {item['task_id']: datetime.fromisoformat(item['last_read_at']).astimezone(timezone.utc) for item in response.data}
        return {}
    except Exception as e:
        st.error(f"Lỗi khi tải trạng thái đã đọc: {e}")
        return {}

def mark_task_as_read(_supabase_client: Client, task_id: int, user_id: str):
    """Upserts the last read time for a user and a task using current UTC time."""
    try:
        # THÊM on_conflict='task_id, user_id' để Supabase biết cách xử lý trùng lặp
        _supabase_client.table('task_read_status').upsert(
            {
                'task_id': task_id,
                'user_id': user_id,
                'last_read_at': datetime.now(timezone.utc).isoformat()
            },
            on_conflict='task_id, user_id'  # Dòng quan trọng được thêm vào
        ).execute()
    except Exception as e:
        # In ra lỗi chi tiết hơn để dễ chẩn đoán nếu vẫn xảy ra
        print(f"Không thể đánh dấu đã đọc cho task {task_id}: {e}")

# HÀM CHẨN ĐOÁN DÀNH RIÊNG CHO MANAGER_APP.PY
def add_comment(task_id: int, user_id: str, content: str, uploaded_file=None):
    """Thêm bình luận mới, với file đính kèm đã được làm sạch tên."""
    attachment_url = None
    attachment_original_name = None 

    if uploaded_file:
        if uploaded_file.size > 2 * 1024 * 1024:
            st.error("Lỗi: Kích thước file không được vượt quá 2MB.")
            return

        # Lưu lại tên gốc để hiển thị
        attachment_original_name = uploaded_file.name
        
        # FIX: Làm sạch tên file trước khi tạo đường dẫn
        sanitized_name = sanitize_filename(uploaded_file.name)
        file_path = f"task_{task_id}/{user_id}_{int(datetime.now().timestamp())}_{sanitized_name}"
        
        try:
            # Dùng client 'supabase_new' cho manager
            supabase_new.storage.from_("task-attachments").upload(
                file=uploaded_file.getvalue(),
                path=file_path,
                file_options={"content-type": uploaded_file.type}
            )
            attachment_url = supabase_new.storage.from_("task-attachments").get_public_url(file_path)
        except Exception as e:
            st.error(f"Lỗi khi tải file lên: {e}")
            return

    try:
        insert_data = {
            'task_id': task_id,
            'user_id': user_id,
            'content': content,
            'attachment_url': attachment_url,
            'attachment_original_name': attachment_original_name
        }
        # Dùng client 'supabase_new' cho manager
        supabase_new.table('comments').insert(insert_data).execute()
        st.cache_data.clear()
        st.toast("Đã gửi bình luận!", icon="💬")
    except Exception as e:
        st.error(f"Lỗi khi thêm bình luận: {e}")
        
def update_account_status(user_id: str, new_status: str):
    """
    Updates the account status for a user in both the profiles table
    and Supabase Auth system.
    'active' -> enables login, 'inactive' -> disables login.
    """
    try:
        ban_duration = '876000h' if new_status == 'inactive' else 'none'
        
        supabase_new.auth.admin.update_user_by_id(
            user_id,
            attributes={'ban_duration': ban_duration}
        )

        supabase_new.table('profiles').update({'account_status': new_status}).eq('id', user_id).execute()
        
        st.cache_data.clear()
        st.toast(f"Đã {'vô hiệu hóa' if new_status == 'inactive' else 'kích hoạt'} tài khoản thành công!", icon="✅")
        st.rerun()

    except Exception as e:
        st.error("Yêu cầu tới Supabase Auth THẤT BẠI!")
        st.exception(e)

def update_task_details(task_id: int, updates: dict):
    """Cập nhật các trường cụ thể cho một công việc."""
    try:
        supabase_new.table('tasks').update(updates).eq('id', task_id).execute()
        st.cache_data.clear()
        st.toast("Cập nhật công việc thành công!", icon="✅")
    except Exception as e:
        st.error(f"Lỗi khi cập nhật công việc: {e}")

def update_task_assignee(task_id: int, new_assignee_id: str):
    """Updates the assignee for a specific task."""
    try:
        supabase_new.table('tasks').update({'assigned_to': new_assignee_id}).eq('id', task_id).execute()
        st.cache_data.clear()
        st.toast("Đã chuyển giao công việc!", icon="🔄")
    except Exception as e:
        st.error(f"Lỗi khi chuyển giao công việc: {e}")

def delete_task(task_id: int):
    """
    Xóa một công việc, các bình luận liên quan (thông qua cascade delete trong CSDL),
    và tất cả các file đính kèm của nó trong Storage.
    """
    try:
        folder_path = f"task_{task_id}"
        attachment_files = supabase_new.storage.from_("task-attachments").list(path=folder_path)

        if attachment_files:
            files_to_remove = [f"{folder_path}/{file['name']}" for file in attachment_files]
            if files_to_remove:
                st.info(f"Đang xóa {len(files_to_remove)} tệp đính kèm liên quan...")
                supabase_new.storage.from_("task-attachments").remove(files_to_remove)
                st.info("Đã xóa thành công các tệp đính kèm.")

        response = supabase_new.table('tasks').delete().eq('id', task_id).execute()

        if hasattr(response, 'error') and response.error:
            raise Exception(f"Lỗi CSDL: {response.error.message}")

        st.cache_data.clear()
        st.toast("Đã xóa công việc và các file đính kèm thành công!", icon="🗑️")

    except Exception as e:
        error_str = str(e)
        if "StorageError" in error_str:
             st.error(f"Lỗi khi xóa file trên Storage: {e}")
        else:
             st.error(f"Lỗi khi xóa công việc: {e}")

def delete_project(project_id: int):
    """Deletes a project if it has no associated tasks."""
    try:
        task_check = supabase_new.table('tasks').select('id', count='exact').eq('project_id', project_id).execute()
        if task_check.count > 0:
            st.error(f"Không thể xóa dự án. Vẫn còn {task_check.count} công việc thuộc dự án này.")
            return
        
        supabase_new.table('projects').delete().eq('id', project_id).execute()
        st.cache_data.clear()
        st.toast("Đã xóa dự án thành công!", icon="🗑️")
    except Exception as e:
        st.error(f"Lỗi khi xóa dự án: {e}")


def delete_employee(user_id: str):
    """
    Xóa người dùng khỏi Supabase Auth và hồ sơ tương ứng trong bảng public.
    """
    try:
        supabase_new.auth.admin.delete_user(user_id)
        profile_response = supabase_new.table('profiles').delete().eq('id', user_id).execute()

        if hasattr(profile_response, 'error') and profile_response.error:
            st.warning(f"Người dùng đã được xóa khỏi hệ thống xác thực, nhưng có lỗi khi xóa hồ sơ: {profile_response.error.message}")

        st.cache_data.clear()
        st.toast("Đã xóa nhân viên thành công!", icon="🗑️")
        if 'user_to_delete' in st.session_state:
            del st.session_state.user_to_delete
        st.rerun()

    except Exception as e:
        error_str = str(e).lower()
        if "violates foreign key constraint" in error_str:
            st.error("Xóa thất bại! Nhân viên này đã có dữ liệu liên quan (công việc đã tạo, bình luận,...).", icon="🛡️")
        else:
            st.error(f"Lỗi khi xóa người dùng: {e}")

        if 'user_to_delete' in st.session_state:
            del st.session_state.user_to_delete
        st.rerun()

def get_or_create_project_in_new_db(project_from_old: dict) -> int:
    ref_id = project_from_old.get('quotation_no')
    if not ref_id: raise ValueError("Dữ liệu dự án từ hệ thống cũ thiếu 'quotation_no'.")
    try:
        response = supabase_new.table('projects').select('id').eq('old_project_ref_id', ref_id).limit(1).execute()
    except Exception as e:
        raise Exception(f"Lỗi mạng khi kiểm tra dự án tồn tại: {e}")
    if not hasattr(response, 'data'): raise Exception("Phản hồi từ Supabase không hợp lệ khi kiểm tra dự án.")
    if response.data: return response.data[0]['id']
    else:
        st.info(f"Dự án với mã '{ref_id}' chưa có. Đang đồng bộ...")
        new_project_data = {
            'project_name': f"{project_from_old.get('customer_name')} - {project_from_old.get('project_type')}",
            'description': f"Dự án được đồng bộ từ hệ thống cũ với mã: {ref_id}",
            'old_project_ref_id': ref_id
        }
        try:
            insert_res = supabase_new.table('projects').insert(new_project_data).execute()
        except Exception as e:
            raise Exception(f"Lỗi mạng khi tạo dự án đồng bộ: {e}")
        if not hasattr(insert_res, 'data'): raise Exception("Phản hồi từ Supabase không hợp lệ khi tạo dự án đồng bộ.")
        if insert_res.data:
            st.success(f"Đồng bộ thành công dự án '{new_project_data['project_name']}'.")
            return insert_res.data[0]['id']
        else:
            error_message = getattr(insert_res.error, 'message', 'Lỗi không xác định')
            raise Exception(f"Không thể tạo dự án đồng bộ: {error_message}")

def change_password(new_password: str):
    """Updates the password for the currently logged-in user."""
    try:
        supabase_auth.auth.update_user({"password": new_password})
        st.success("✅ Đổi mật khẩu thành công! Vui lòng sử dụng mật khẩu mới ở lần đăng nhập sau.")
    except Exception as e:
        st.error(f"Lỗi khi đổi mật khẩu: {e}")

def reset_user_password(user_id: str, new_password: str):
    """Resets the password for a given user by an admin."""
    try:
        supabase_new.auth.admin.update_user_by_id(
            user_id,
            attributes={'password': new_password}
        )
        st.success(f"🔑 Đã đặt lại mật khẩu cho người dùng thành công!")
    except Exception as e:
        st.error(f"Lỗi khi đặt lại mật khẩu: {e}")


# --- MAIN APP LOGIC ---
if 'user' not in st.session_state:
    st.session_state.user = None
if 'manager_profile' not in st.session_state:
    st.session_state.manager_profile = None

# --- Login UI ---
if st.session_state.user is None:
    if 'logout_message' in st.session_state:
        st.warning(st.session_state.logout_message)
        del st.session_state.logout_message # Xóa thông báo để không hiển thị lại

    st.title("👨‍💼 Đăng nhập Trang Quản lý")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Mật khẩu", type="password")
        submitted = st.form_submit_button("Đăng nhập")
        if submitted:
            try:
                user_response = supabase_auth.auth.sign_in_with_password({"email": email, "password": password})
                user = user_response.user
                
                profile_res = supabase_new.table('profiles').select('id, full_name, role, account_status').eq('id', user.id).single().execute()
                profile_data = profile_res.data
                
                # <<< THAY ĐỔI: Cho phép cả 'manager' và 'admin' đăng nhập
                if profile_data and profile_data.get('role') in ['manager', 'admin']:
                    if profile_data.get('account_status') == 'inactive':
                        st.error("Tài khoản của bạn đã bị vô hiệu hóa. Vui lòng liên hệ quản trị viên.")
                        supabase_auth.auth.sign_out()
                    else:
                        st.session_state.user = user
                        st.session_state.manager_profile = profile_data
                        st.rerun()
                else:
                    st.error("Truy cập bị từ chối. Bạn không có quyền quản lý hoặc quản trị.")
                    supabase_auth.auth.sign_out()
            except Exception as e:
                st.error("Email hoặc mật khẩu không đúng. Vui lòng thử lại.")

# --- Main App UI (after login) ---
else:
    # ===================================================================
    # BẮT ĐẦU: LOGIC KIỂM TRA KHÔNG HOẠT ĐỘNG
    # ===================================================================
    TIMEOUT_IN_SECONDS = 1800 # 30 phút

    is_expired = False
    if 'last_activity_time' in st.session_state:
        idle_duration = time.time() - st.session_state.last_activity_time
        if idle_duration > TIMEOUT_IN_SECONDS:
            is_expired = True

    if is_expired:
        st.error(
            "**Phiên làm việc đã hết hạn!** "
            "Để bảo mật, mọi thao tác đã được vô hiệu hóa. "
            "Vui lòng sao chép lại nội dung bạn đang soạn (nếu có), sau đó **Đăng xuất** và đăng nhập lại."
        )
    else:
        st.session_state.last_activity_time = time.time()
    # ===================================================================
    # KẾT THÚC: LOGIC KIỂM TRA KHÔNG HOẠT ĐỘNG
    # ===================================================================
    manager_profile = st.session_state.manager_profile
    user = st.session_state.user
    
    # <<< THAY ĐỔI: Lấy vai trò của người dùng đang đăng nhập
    current_user_role = manager_profile.get('role')

    st.sidebar.title(f"Xin chào, {manager_profile.get('full_name', user.email)}!")
    st.sidebar.caption(f"Vai trò: {current_user_role.capitalize()}") # Hiển thị vai trò
    if st.sidebar.button("Đăng xuất", use_container_width=True):
        supabase_auth.auth.sign_out()
        st.session_state.user = None
        st.session_state.manager_profile = None
        st.rerun()
    
    if st.sidebar.button("🔄 Làm mới dữ liệu", use_container_width=True):
        st.cache_data.clear()
        st.toast("Đã làm mới dữ liệu!", icon="🔄")
        st.rerun()

    st.title("👨‍💼 Hệ thống Quản lý Công việc")

    # --- DATA LOADING ---
    projects_data_old = fetch_old_projects(supabase_old)
    all_profiles_data = fetch_all_profiles(supabase_new)
    all_tasks = fetch_all_tasks_and_details(supabase_new)
    all_projects_new = fetch_all_projects_new(supabase_new)
    active_employees = [p for p in all_profiles_data if p.get('role') == 'employee' and p.get('account_status') == 'active'] if all_profiles_data else []


    # --- Tabs for navigation ---
    tab_tasks, tab_employees, tab_projects, tab_settings = st.tabs([
        "Công việc & Giao việc", 
        "Quản lý Nhân viên", 
        "Quản lý Dự án", 
        "⚙️ Cài đặt Tài khoản"
    ])

    with tab_tasks:
        st.header("✍️ Giao việc mới")
        if not projects_data_old:
            st.warning("Cần có dữ liệu dự án từ hệ thống cũ để có thể giao việc.")
        elif not active_employees:
            st.warning("CHƯA CÓ NHÂN VIÊN (active employee) TRONG HỆ THỐNG MỚI.")
        else:
            du_an_dang_thuc_hien = [p for p in projects_data_old if p.get('status') == 'Đang thực hiện']
            with st.form("new_task_form", clear_on_submit=True):
                st.subheader("Nhập thông tin công việc")
                col1_task, col2_task = st.columns(2)
                with col1_task:
                    project_options_map = {f"{p['customer_name']} - {p['project_type']} (Mã: {p['quotation_no']})": p for p in du_an_dang_thuc_hien}
                    selected_project_display = st.selectbox("1. Chọn Dự án/Vụ việc:", options=project_options_map.keys(),disabled=is_expired)
                    task_name = st.text_input("2. Tên công việc:", placeholder="VD: Soạn thảo hợp đồng mua bán...",disabled=is_expired)
                    employee_options = {f"{e['full_name']} ({e['email']})": e['id'] for e in active_employees}
                    selected_employee_display = st.selectbox("3. Giao cho nhân viên:", options=employee_options.keys(), disabled=is_expired)
                with col2_task:
                    priority = st.selectbox("4. Độ ưu tiên:", options=['Medium', 'High', 'Low'], index=0, disabled=is_expired)
                    local_tz = ZoneInfo("Asia/Ho_Chi_Minh")
                    current_time_vn = datetime.now(local_tz)
                    deadline_date = st.date_input("5. Hạn chót (ngày):", min_value=current_time_vn.date(), disabled=is_expired)
                    deadline_hour = st.time_input("6. Hạn chót (giờ):", value=current_time_vn.time(), disabled=is_expired)
                    description = st.text_area("7. Mô tả chi tiết:", height=150, disabled=is_expired)
                submitted = st.form_submit_button("🚀 Giao việc", disabled= is_expired)
                if submitted and not is_expired:
                    due_date = datetime.combine(deadline_date, deadline_hour)
                    if not task_name:
                        st.error("Vui lòng nhập tên công việc!")
                    else:
                        try:
                            selected_project_from_old = project_options_map[selected_project_display]
                            project_id_in_new = get_or_create_project_in_new_db(selected_project_from_old)
                            assigned_to_id = employee_options[selected_employee_display]
                            creator_id = user.id
                            due_date_iso = due_date.isoformat()
                            new_task_data = {'task_name': task_name, 'description': description, 'priority': priority, 'due_date': due_date_iso, 'assigned_to': assigned_to_id, 'created_by': creator_id, 'status': 'To Do', 'project_id': project_id_in_new}
                            response = supabase_new.table('tasks').insert(new_task_data).execute()
                            if response.data:
                                st.success(f"Giao việc '{task_name}' cho nhân viên thành công!")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(f"Có lỗi xảy ra khi giao việc. Chi tiết: {response.error.message if response.error else 'Lỗi không xác định'}")
                        except Exception as e:
                            st.error(f"Lỗi hệ thống: {e}")

        st.markdown("---")
        st.header("📊 Danh sách công việc đã giao")
        st.markdown("""
        <style>
        .color-box {
            width: 15px;
            height: 15px;
            display: inline-block;
            border: 1px solid #ccc;
            vertical-align: middle;
            margin-right: 5px;
        }
        </style>
        <b>Chú thích Deadline:</b>
        <span class="color-box" style="background-color: #ffebee;"></span> < 3 ngày
        <span class="color-box" style="background-color: #fff3e0;"></span> 3-7 ngày
        <span class="color-box" style="background-color: #fffde7;"></span> 7-15 ngày
        <span class="color-box" style="background-color: #e8f5e9;"></span> > 15 ngày
        """, unsafe_allow_html=True)
        st.text("") 

        group_by = st.radio(
            "Nhóm công việc theo:",
            ('Dự án', 'Nhân viên'),
            horizontal=True,
            key="grouping_tasks"
        )

        if not all_tasks:
            st.info("Chưa có công việc nào được giao trong hệ thống.")
        else:
            local_tz = ZoneInfo("Asia/Ho_Chi_Minh")
            read_statuses = fetch_read_statuses(supabase_new, user.id) 
            
            # --- Bước 1: Nhóm các công việc lại như cũ ---
            grouped_tasks = defaultdict(list)
            if group_by == 'Dự án':
                for task in all_tasks:
                    project_info = task.get('projects')
                    key = (project_info.get('project_name', 'Dự án không tên'), project_info.get('old_project_ref_id')) if project_info else ('Không thuộc dự án cụ thể', None)
                    grouped_tasks[key].append(task)
            else: # Nhóm theo Nhân viên
                for task in all_tasks:
                    grouped_tasks[task.get('assignee_name', 'Chưa giao cho ai')].append(task)

            # --- Bước 2: Tạo hộp tìm kiếm/chọn lựa ---
            group_keys = sorted(grouped_tasks.keys(), key=str)
            
            if group_by == 'Dự án':
                # Định dạng lại tên hiển thị cho các dự án
                options_map = {f"{name} (Mã: {code})" if code else name: key for key, (name, code) in zip(group_keys, group_keys)}
                label = "🔍 Tìm và nhảy đến Dự án"
            else: # Nhóm theo Nhân viên
                # Tên nhân viên là key
                options_map = {key: key for key in group_keys}
                label = "🔍 Tìm và nhảy đến Nhân viên"
                
            # Thêm lựa chọn "Hiển thị tất cả" vào đầu danh sách
            options_list = ["--- Hiển thị tất cả ---"] + list(options_map.keys())
            
            selected_option = st.selectbox(label, options=options_list)
            st.divider()

            # --- Bước 3: Lọc dữ liệu dựa trên lựa chọn của người dùng ---
            if selected_option and selected_option != "--- Hiển thị tất cả ---":
                selected_key = options_map[selected_option]
                tasks_to_display = {selected_key: grouped_tasks[selected_key]}
            else:
                # Nếu không chọn gì hoặc chọn "Hiển thị tất cả" thì giữ nguyên
                tasks_to_display = grouped_tasks

            # --- Bước 4: Hiển thị danh sách công việc đã được lọc ---
            sorted_grouped_tasks = sorted(tasks_to_display.items(), key=lambda item: str(item[0]))

            if not sorted_grouped_tasks:
                st.info("Không tìm thấy kết quả phù hợp.")
                
            for key, tasks_in_group in sorted_grouped_tasks:
                if group_by == 'Dự án':
                    project_name, project_code = key
                    display_title = f"Dự án: {project_name}" + (f" (Mã: {project_code})" if project_code else "")
                else:
                    display_title = f"Nhân viên: {key}"
                st.subheader(display_title)
                
                # Sắp xếp theo deadline tăng dần, nhiệm vụ không có deadline sẽ xuống cuối
                sorted_tasks = sorted(tasks_in_group, key=lambda t: (t.get('due_date') is None, t.get('due_date')))
                task_counter = 0

                for task in sorted_tasks:
                    # --- Phần code hiển thị chi tiết mỗi công việc (giữ nguyên như cũ) ---
                    task_counter += 1
                    comments = fetch_comments(task['id'])
                    
                    status_icon = ""
                    has_new_message = False
                    last_read_time_utc = read_statuses.get(task['id'], datetime.fromtimestamp(0, tz=timezone.utc))
                    last_event_time_utc = datetime.fromisoformat(task['created_at']).astimezone(timezone.utc)
                    if comments:
                        last_comment_time_utc = datetime.fromisoformat(comments[0]['created_at']).astimezone(timezone.utc)
                        if last_comment_time_utc > last_event_time_utc:
                            last_event_time_utc = last_comment_time_utc
                    if comments and comments[0]['user_id'] == user.id:
                        status_icon = "✅ Đã trả lời"
                    elif last_event_time_utc > last_read_time_utc:
                        status_icon = "💬 Mới!"
                        has_new_message = True
                    elif comments:
                        status_icon = "✔️ Đã xem"

                    is_overdue = False
                    if task.get('due_date'):
                        try:
                            due_date = datetime.fromisoformat(task['due_date']).astimezone(local_tz)
                            if due_date < datetime.now(local_tz):
                                is_overdue = True
                        except (ValueError, TypeError):
                            is_overdue = False

                    line_1 = f"**Task {task_counter}. {task['task_name']}**"
                    try:
                        formatted_due_date = datetime.fromisoformat(task['due_date']).astimezone(local_tz).strftime('%d/%m/%Y, %H:%M')
                    except (ValueError, TypeError):
                        formatted_due_date = 'N/A'
                    
                    line_2_parts = [status_icon, f"Trạng thái thực hiện: *{task['status']}*"]
                    if group_by == 'Dự án':
                        line_2_parts.append(f"Người thực hiện: *{task.get('assignee_name', 'N/A')}*")
                    else:
                        project_name_display = task.get('projects', {}).get('project_name', 'N/A')
                        line_2_parts.append(f"Dự án: *_{project_name_display}_*")
                    
                    line_2_parts.append(f"Deadline: *{formatted_due_date}*")
                    line_2 = " | ".join(filter(None, line_2_parts))

                    deadline_color = get_deadline_color(task.get('due_date'))
                    st.markdown(f'<div style="background-color: {deadline_color}; border-radius: 7px; padding: 10px; margin-bottom: 10px;">', unsafe_allow_html=True)
                    
                    st.markdown(f"<span style='color: blue;'>{line_1}</span>", unsafe_allow_html=True)
                    st.markdown(line_2)

                    if is_overdue and task.get('status') != 'Done':
                        st.markdown("<span style='color: red;'><b>Lưu ý: Nhiệm vụ đã quá hạn hoặc đã làm xong nhưng nhân viên chưa chuyển trạng thái Done</b></span>", unsafe_allow_html=True)

                    with st.expander("Chi tiết & Thảo luận"):
                        # ... (Toàn bộ code trong expander giữ nguyên y hệt như cũ) ...
                        if has_new_message:
                            if st.button("✔️ Đánh dấu đã đọc", key=f"read_mgr_{task['id']}", help="Bấm vào đây để xác nhận bạn đã xem tin nhắn mới nhất.", disabled=is_expired) and not is_expired:
                                mark_task_as_read(supabase_new, task['id'], user.id)
                                fetch_read_statuses.clear()
                                st.rerun()
                            st.divider()

                        if st.toggle("✏️ Chỉnh sửa công việc", key=f"edit_toggle_{task['id']}",disabled= is_expired):
                            with st.form(key=f"edit_form_{task['id']}", clear_on_submit=True):
                                st.markdown("##### **📝 Cập nhật thông tin công việc**")
                                new_task_name = st.text_input("Tên công việc", value=task.get('task_name', ''))
                                project_options_map_edit = {p['project_name']: p['id'] for p in all_projects_new} if all_projects_new else {}
                                project_names = list(project_options_map_edit.keys())
                                employee_options_map = {e['full_name']: e['id'] for e in active_employees}
                                employee_names = list(employee_options_map.keys())
                                priorities = ['Low', 'Medium', 'High']
                                current_project_name = task.get('projects', {}).get('project_name')
                                try:
                                    default_proj_index = project_names.index(current_project_name) if current_project_name else 0
                                except ValueError:
                                    default_proj_index = 0
                                current_assignee_name = task.get('assignee_name')
                                try:
                                    default_employee_index = employee_names.index(current_assignee_name) if current_assignee_name in employee_names else 0
                                except ValueError:
                                    default_employee_index = 0
                                try:
                                    default_prio_index = priorities.index(task.get('priority')) if task.get('priority') else 1
                                except ValueError:
                                    default_prio_index = 1
                                try:
                                    current_due_datetime = datetime.fromisoformat(task['due_date']).astimezone(local_tz)
                                except (ValueError, TypeError):
                                    current_due_datetime = datetime.now(local_tz)
                                col1, col2 = st.columns(2)
                                with col1:
                                    new_project_name = st.selectbox("Dự án", options=project_names, index=default_proj_index, key=f"proj_edit_{task['id']}")
                                with col2:
                                    new_assignee_name = st.selectbox("Giao cho nhân viên", options=employee_names, index=default_employee_index, key=f"assignee_edit_{task['id']}")
                                col3, col4, col5 = st.columns(3)
                                with col3:
                                    new_priority = st.selectbox("Độ ưu tiên", options=priorities, index=default_prio_index, key=f"prio_edit_{task['id']}")
                                with col4:
                                    new_due_date = st.date_input("Hạn chót (ngày)", value=current_due_datetime.date(), key=f"date_edit_{task['id']}")
                                with col5:
                                    new_due_time = st.time_input("Hạn chót (giờ)", value=current_due_datetime.time(), key=f"time_edit_{task['id']}")
                                submitted_edit = st.form_submit_button("💾 Lưu thay đổi", use_container_width=True, type="primary",disabled=is_expired)
                                if submitted_edit and not is_expired:
                                    updates_dict = {}
                                    if new_task_name and new_task_name != task.get('task_name'):
                                        updates_dict['task_name'] = new_task_name
                                    selected_project_id = project_options_map_edit.get(new_project_name)
                                    if selected_project_id and selected_project_id != task.get('project_id'):
                                        updates_dict['project_id'] = selected_project_id
                                    selected_employee_id = employee_options_map.get(new_assignee_name)
                                    if selected_employee_id and selected_employee_id != task.get('assigned_to'):
                                        updates_dict['assigned_to'] = selected_employee_id
                                    if new_priority != task.get('priority'):
                                        updates_dict['priority'] = new_priority
                                    naive_deadline = datetime.combine(new_due_date, new_due_time)
                                    aware_deadline = naive_deadline.replace(tzinfo=local_tz)
                                    if aware_deadline.isoformat() != task.get('due_date'):
                                        updates_dict['due_date'] = aware_deadline.isoformat()
                                    if updates_dict:
                                        update_task_details(task['id'], updates_dict)
                                        st.toast("Cập nhật thành công!", icon="✅")
                                        st.rerun()
                                    else:
                                        st.toast("Không có thay đổi nào để lưu.", icon="🤷‍♂️")

                        st.divider()
                        st.markdown("##### **Chi tiết & Thảo luận**")
                        task_cols = st.columns([3, 1])
                        with task_cols[1]:
                            if st.button("🗑️ Xóa Công việc", key=f"delete_task_{task['id']}", type="secondary", use_container_width=True,disabled=is_expired) and not is_expired:
                                st.session_state[f"confirm_delete_task_{task['id']}"] = True
                        if st.session_state.get(f"confirm_delete_task_{task['id']}"):
                            with st.warning(f"Bạn có chắc muốn xóa vĩnh viễn công việc **{task['task_name']}**?"):
                                c1, c2 = st.columns(2)
                                if c1.button("✅ Xóa", key=f"confirm_del_btn_{task['id']}", type="primary") and not is_expired:
                                    delete_task(task['id'])
                                    del st.session_state[f"confirm_delete_task_{task['id']}"]
                                    st.rerun()
                                if c2.button("❌ Hủy", key=f"cancel_del_btn_{task['id']}"):
                                    del st.session_state[f"confirm_delete_task_{task['id']}"]
                                    st.rerun()
                        meta_cols = st.columns(3)
                        meta_cols[0].markdown("**Độ ưu tiên**")
                        meta_cols[0].write(task.get('priority', 'N/A'))
                        meta_cols[1].markdown("**Hạn chót**")
                        try:
                            formatted_due_date_detail = datetime.fromisoformat(task['due_date']).astimezone(local_tz).strftime('%d/%m/%Y, %H:%M')
                        except (ValueError, TypeError):
                            formatted_due_date_detail = task.get('due_date', 'N/A')
                        meta_cols[1].write(formatted_due_date_detail)
                        meta_cols[2].markdown("**Người giao**")
                        meta_cols[2].write(task.get('creator_name', 'N/A'))
                        if task['description']:
                            st.markdown("**Mô tả:**")
                            st.info(task['description'])
                        st.divider()
                        st.markdown("##### **Thảo luận**")
                        with st.container(height=250):
                            if not comments:
                                st.info("Chưa có bình luận nào.", icon="📄")
                            else:
                                for comment in comments:
                                    commenter_name = comment.get('profiles', {}).get('full_name', "Người dùng ẩn")
                                    is_manager_comment = 'manager' in comment.get('profiles', {}).get('role', 'employee')
                                    comment_time_local = datetime.fromisoformat(comment['created_at']).astimezone(local_tz).strftime('%H:%M, %d/%m/%Y')
                                    st.markdown(f"<div style='border-left: 3px solid {'#ff4b4b' if is_manager_comment else '#007bff'}; padding-left: 10px; margin-bottom: 10px;'><b>{commenter_name}</b> {'(Quản lý)' if is_manager_comment else ''} <span style='font-size: 0.8em; color: gray;'><i>({comment_time_local})</i></span>:<br>{comment['content']}</div>", unsafe_allow_html=True)
                                    if comment.get('attachment_url'):
                                        url = comment['attachment_url']
                                        file_name = comment.get('attachment_original_name', 'downloaded_file')
                                        try:
                                            response = requests.get(url)
                                            response.raise_for_status() 
                                            st.download_button(
                                                label="📂 Tải file đính kèm",
                                                data=response.content,
                                                file_name=file_name,
                                                key=f"download_manager_{task['id']}_{comment['id']}"
                                            )
                                            st.caption(f"{file_name}")
                                        except requests.exceptions.RequestException as e:
                                            st.error(f"Không thể tải tệp: {e}")
                        with st.form(key=f"comment_form_manager_{task['id']}", clear_on_submit=True):
                            comment_content = st.text_area("Thêm bình luận:", key=f"comment_text_manager_{task['id']}", label_visibility="collapsed", placeholder="Nhập bình luận của bạn...", disabled=is_expired)
                            uploaded_file = st.file_uploader("Đính kèm file (Word, RAR, ZIP <2MB)", type=['doc', 'docx', 'rar', 'zip'], accept_multiple_files=False, key=f"file_manager_{task['id']}", disabled=is_expired)
                            submitted_comment = st.form_submit_button("Gửi bình luận",disabled=is_expired)
                            if submitted_comment and is_expired and (comment_content or uploaded_file):
                                st.warning("⚠️ Nội dung của bạn CHƯA ĐƯỢC GỬI do phiên làm việc đã hết hạn. Dưới đây là bản sao để bạn tiện lưu lại:")
                                if comment_content:
                                    st.code(comment_content, language=None)
                                if uploaded_file:
                                    st.info(f"Bạn cũng đã đính kèm tệp: **{uploaded_file.name}**. Vui lòng tải lại tệp này sau khi đăng nhập.")
                            if submitted_comment and (comment_content or uploaded_file) and not is_expired:
                                add_comment(task['id'], manager_profile['id'], comment_content, uploaded_file)
                                st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)


    with tab_employees:
        st.header("👥 Quản lý Nhân viên")
        
        # <<< THAY ĐỔI: Chỉ admin mới có quyền thêm nhân viên
        if current_user_role == 'admin':
            with st.expander("➕ Thêm nhân viên mới"):
                with st.form("new_employee_form", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    with col1:
                        full_name = st.text_input("Họ và tên:", placeholder="Nguyễn Văn A", disabled=is_expired)
                        email = st.text_input("Email:", placeholder="email@congty.com", disabled=is_expired)
                    with col2:
                        password = st.text_input("Mật khẩu tạm thời:", type="password", disabled=is_expired)
                        # <<< THAY ĐỔI: Thêm vai trò 'admin' và format tên cho dễ đọc
                        role = st.selectbox(
                            "Vai trò:", 
                            options=['employee', 'manager', 'admin'], 
                            format_func=lambda x: "Nhân viên" if x == 'employee' else ("Quản lý" if x == 'manager' else "Quản trị viên"),
                            disabled=is_expired
                        )
                    
                    add_employee_submitted = st.form_submit_button("Thêm nhân viên", use_container_width=True, disabled=is_expired)
                    if add_employee_submitted and not is_expired:
                        if not full_name or not email or not password:
                            st.error("Vui lòng điền đầy đủ thông tin: Họ tên, Email và Mật khẩu.")
                        else:
                            try:
                                new_user_res = supabase_new.auth.admin.create_user({"email": email, "password": password, "user_metadata": {'full_name': full_name}, "email_confirm": True})
                                new_user = new_user_res.user
                                if new_user:
                                    st.success(f"Tạo tài khoản cho '{full_name}' thành công!")
                                    # Cập nhật cả role và full_name vào bảng profiles
                                    profile_update_res = supabase_new.table('profiles').update({'role': role, 'full_name': full_name, 'account_status': 'active'}).eq('id', new_user.id).execute()
                                    st.cache_data.clear()
                                    st.rerun()
                                else:
                                    st.error("Có lỗi xảy ra từ Supabase khi tạo người dùng.")
                            except Exception as e:
                                st.error(f"Lỗi hệ thống: {'Email đã tồn tại' if 'User already exists' in str(e) else e}")
            st.markdown("---")


        st.subheader("Danh sách nhân viên hiện tại")

        if 'user_to_reset_pw' in st.session_state and st.session_state.user_to_reset_pw:
            user_to_reset = st.session_state.user_to_reset_pw
            with st.container(border=True):
                st.subheader(f"🔑 Đặt lại mật khẩu cho {user_to_reset.get('full_name')}")
                with st.form(key=f"reset_pw_form_{user_to_reset['id']}"):
                    new_password = st.text_input("Nhập mật khẩu mới", type="password")
                    submitted = st.form_submit_button("Xác nhận đặt lại mật khẩu", type="primary", use_container_width=True)
                    if submitted:
                        if not new_password or len(new_password) < 6:
                            st.error("Mật khẩu phải có ít nhất 6 ký tự.")
                        else:
                            reset_user_password(user_to_reset['id'], new_password)
                            del st.session_state.user_to_reset_pw
                            st.rerun()
                if st.button("Hủy bỏ", key="cancel_reset_pw"):
                    del st.session_state.user_to_reset_pw
                    st.rerun()
            st.divider()

        if 'user_to_delete' in st.session_state and st.session_state.user_to_delete:
            user_name = st.session_state.user_to_delete['name']
            with st.container(border=True):
                st.warning(f"**Xác nhận xóa người dùng**", icon="⚠️")
                st.write(f"Bạn có chắc chắn muốn xóa vĩnh viễn nhân viên **{user_name}**?")
                st.info("Lưu ý: Bạn sẽ không thể xóa nhân viên đã có dữ liệu liên quan.", icon="ℹ️")
                col1, col2 = st.columns(2)
                if col1.button("✅ Xác nhận Xóa", use_container_width=True, type="primary"):
                    delete_employee(st.session_state.user_to_delete['id'])
                if col2.button("❌ Hủy", use_container_width=True):
                    del st.session_state.user_to_delete
                    st.rerun()

        if all_profiles_data:
            c1, c2, c3, c4 = st.columns([2, 3, 2, 3])
            c1.markdown("**Họ và tên**")
            c2.markdown("**Email**")
            c3.markdown("**Trạng thái**")
            c4.markdown("**Hành động**")
            st.divider()

            for u in all_profiles_data:
                # Không cho phép admin tự thao tác với chính tài khoản của mình
                if u['id'] == user.id:
                    continue

                col1, col2, col3, col4 = st.columns([2, 3, 2, 3])
                with col1:
                    st.write(u.get('full_name', 'N/A'))
                    # <<< THAY ĐỔI: Hiển thị đúng tên vai trò
                    role_display = "Quản trị viên" if u.get('role') == 'admin' else ("Quản lý" if u.get('role') == 'manager' else "Nhân viên")
                    st.caption(f"Vai trò: {role_display}")
                with col2:
                    st.write(u.get('email', 'N/A'))
                with col3:
                    status = u.get('account_status', 'N/A')
                    st.write(f"🟢 Hoạt động" if status == 'active' else f"⚪ Vô hiệu hóa")
                with col4:
                    # <<< THAY ĐỔI: Chỉ admin mới thấy các nút hành động
                    if current_user_role == 'admin':
                        action_cols = st.columns([1, 1, 1])
                        # Nút Kích hoạt / Vô hiệu hóa
                        if status == 'active':
                            if action_cols[0].button("Vô hiệu hóa", key=f"deact_{u['id']}", use_container_width=True, disabled=is_expired) and not is_expired:
                                update_account_status(u['id'], 'inactive')
                        else:
                            if action_cols[0].button("Kích hoạt", key=f"act_{u['id']}", use_container_width=True, type="primary", disabled=is_expired) and not is_expired:
                                update_account_status(u['id'], 'active')
                        
                        # Nút Đặt mật khẩu
                        if action_cols[1].button("🔑 Đặt MK", key=f"reset_pw_{u['id']}", use_container_width=True, disabled=is_expired) and not is_expired:
                            st.session_state.user_to_reset_pw = u
                            st.rerun()

                        # Nút Xóa
                        if action_cols[2].button("🗑️ Xóa", key=f"del_{u['id']}", use_container_width=True, disabled=is_expired) and not is_expired:
                            st.session_state.user_to_delete = {'id': u['id'], 'name': u.get('full_name', 'N/A')}
                            st.rerun()
                st.divider()
        else:
            st.info("Chưa có nhân viên nào trong hệ thống mới.")


    with tab_projects:
        st.header("🗂️ Quản lý Dự án")
        st.info("Tại đây bạn có thể xóa các dự án đã hoàn thành và không còn công việc nào liên quan.", icon="ℹ️")

        if not all_projects_new:
            st.warning("Không có dự án nào trong hệ thống mới.")
        else:
            if 'project_to_delete' in st.session_state and st.session_state.project_to_delete:
                project_name = st.session_state.project_to_delete['name']
                with st.container(border=True):
                    st.warning(f"**Xác nhận xóa dự án**", icon="⚠️")
                    st.write(f"Bạn có chắc chắn muốn xóa vĩnh viễn dự án **{project_name}**?")
                    col1, col2 = st.columns(2)
                    if col1.button("✅ Xác nhận Xóa Dự án", use_container_width=True, type="primary") and not is_expired:
                        delete_project(st.session_state.project_to_delete['id'])
                        del st.session_state.project_to_delete
                        st.rerun()
                    if col2.button("❌ Hủy", use_container_width=True):
                        del st.session_state.project_to_delete
                        st.rerun()

            df_projects = pd.DataFrame(all_projects_new)
            df_projects = df_projects.rename(columns={'project_name': 'Tên Dự án', 'description': 'Mô tả', 'created_at': 'Ngày tạo'})
            
            c1, c2, c3 = st.columns([3, 4, 1])
            c1.markdown("**Tên Dự án**")
            c2.markdown("**Mô tả**")
            c3.markdown("**Hành động**")

            for index, row in df_projects.iterrows():
                c1_proj, c2_proj, c3_proj = st.columns([3, 4, 1])
                c1_proj.write(row['Tên Dự án'])
                c2_proj.caption(row['Mô tả'])
                # <<< THAY ĐỔI: Chỉ admin mới có quyền xóa dự án
                if current_user_role == 'admin':
                    if c3_proj.button("🗑️ Xóa", key=f"delete_project_{row['id']}", type="secondary",disabled=is_expired):
                        st.session_state.project_to_delete = {'id': row['id'], 'name': row['Tên Dự án']}
                        st.rerun()

        st.markdown("---")
        with st.expander("📋 Danh sách Dự án từ Hệ thống Cũ (để tham chiếu)", expanded=False):
            if projects_data_old:
                df_projects_old = pd.DataFrame(projects_data_old)
                if 'status' in df_projects_old.columns:
                    all_statuses = df_projects_old['status'].dropna().unique().tolist()
                    selected_statuses = st.multiselect("Lọc theo trạng thái dự án:", options=all_statuses, default=all_statuses, key="old_project_filter")
                    df_display = df_projects_old[df_projects_old['status'].isin(selected_statuses)]
                else:
                    df_display = df_projects_old
                df_display = df_display.rename(columns={'quotation_no': 'Số báo giá', 'customer_name': 'Tên khách hàng', 'project_type': 'Loại dự án', 'status': 'Trạng thái'})
                cols_to_display = [col for col in ['Số báo giá', 'Tên khách hàng', 'Loại dự án', 'Trạng thái'] if col in df_display.columns]
                st.dataframe(df_display[cols_to_display], use_container_width=True)
            else:
                st.warning("Không tìm thấy dự án nào trong hệ thống cũ.")
    
    with tab_settings:
        st.header("⚙️ Cài đặt Tài khoản của bạn")
        st.subheader("Thay đổi mật khẩu")

        with st.form("change_password_form", clear_on_submit=True):
            new_password = st.text_input("Mật khẩu mới", type="password", disabled=is_expired)
            confirm_password = st.text_input("Xác nhận mật khẩu mới", type="password", disabled=is_expired)
            submitted = st.form_submit_button("Lưu thay đổi",disabled=is_expired)

            if submitted and not is_expired:
                if not new_password or not confirm_password:
                    st.warning("Vui lòng nhập đầy đủ mật khẩu mới và xác nhận.")
                elif new_password != confirm_password:
                    st.error("Mật khẩu xác nhận không khớp!")
                elif len(new_password) < 6:
                     st.error("Mật khẩu phải có ít nhất 6 ký tự.")
                else:
                    change_password(new_password)