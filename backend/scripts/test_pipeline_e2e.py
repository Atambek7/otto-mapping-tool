import requests
from auth import create_access_token
from datetime import timedelta

BASE_URL = "http://127.0.0.1:8001"

def run_tests():
    print("--- 1. Authenticating ---")
    token = create_access_token({"sub": "yana"}, expires_delta=timedelta(hours=1))
    headers = {"Authorization": f"Bearer {token}"}
    print("Generated token for user 'yana' successfully!")

    factory_id = "503718"

    print("\n--- 2. Testing /pipeline/preview ---")
    preview_res = requests.post(
        f"{BASE_URL}/pipeline/preview/{factory_id}",
        headers=headers,
        json={
            "account_name": "JV",
            "days": 7,
            "daily_time": "08:00",
            "timezone": "Europe/Berlin"
        }
    )
    assert preview_res.status_code == 200, f"Preview failed: {preview_res.text}"
    prev_json = preview_res.json()
    print(f"Preview OK: Total products = {prev_json['total_products']}, Days = {prev_json['days']}")
    assert len(prev_json["days_summary"]) == 7

    print("\n--- 3. Testing Strict Account Validation (No default fallback) ---")
    bad_req = requests.post(
        f"{BASE_URL}/pipeline/preview/{factory_id}",
        headers=headers,
        json={"days": 7}  # Missing account_name
    )
    assert bad_req.status_code == 422, f"Expected 422 validation error, got: {bad_req.status_code}"
    print("Strict account validation verified: 422 Unprocessable Entity when account_name is missing.")

    print("\n--- 4. Testing /pipeline/create ---")
    create_res = requests.post(
        f"{BASE_URL}/pipeline/create/{factory_id}",
        headers=headers,
        json={
            "account_name": "JV",
            "days": 7,
            "daily_time": "08:00",
            "timezone": "Europe/Berlin",
            "executor_type": "batch_file"
        }
    )
    assert create_res.status_code == 200, f"Create plan failed: {create_res.text}"
    plan_data = create_res.json()
    plan_id = plan_data["plan_id"]
    print(f"Created Plan ID: {plan_id}, status: {plan_data['status']}")

    print("\n--- 5. Testing Active Duplicate Prevention ---")
    dup_res = requests.post(
        f"{BASE_URL}/pipeline/create/{factory_id}",
        headers=headers,
        json={
            "account_name": "JV",
            "days": 7,
            "daily_time": "08:00",
            "timezone": "Europe/Berlin"
        }
    )
    assert dup_res.status_code == 400, f"Expected 400 for duplicate active plan, got {dup_res.status_code}"
    print("Duplicate active plan prevention verified: 400 Bad Request.")

    print("\n--- 6. Testing /pipeline/plans ---")
    plans_res = requests.get(f"{BASE_URL}/pipeline/plans?account_name=JV&factory_id={factory_id}", headers=headers)
    assert plans_res.status_code == 200, f"List plans failed: {plans_res.text}"
    plans = plans_res.json()
    assert any(p["id"] == plan_id for p in plans)
    print(f"Plans retrieved: found plan {plan_id} with progress: {plans[0]['progress']}")

    print("\n--- 7. Testing Pause & Resume ---")
    pause_res = requests.post(f"{BASE_URL}/pipeline/plan/{plan_id}/pause", headers=headers)
    assert pause_res.status_code == 200
    assert pause_res.json()["status"] == "paused"
    print("Plan paused successfully.")

    resume_res = requests.post(f"{BASE_URL}/pipeline/plan/{plan_id}/resume", headers=headers)
    assert resume_res.status_code == 200
    assert resume_res.json()["status"] == "active"
    print("Plan resumed successfully.")

    print("\n--- 8. Testing /pipeline/plan/{plan_id} details ---")
    detail_res = requests.get(f"{BASE_URL}/pipeline/plan/{plan_id}", headers=headers)
    assert detail_res.status_code == 200
    details = detail_res.json()
    print(f"Plan days count: {len(details['days'])}")

    print("\n--- 9. Testing Cancel & Cleanup ---")
    cancel_res = requests.post(f"{BASE_URL}/pipeline/plan/{plan_id}/cancel", headers=headers)
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"
    print("Plan cancelled successfully.")

    print("\n=== ALL PIPELINE TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_tests()
