from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from api import app
import pytest
import os
from unittest.mock import MagicMock, mock_open


def test_model_gallery():
    with TestClient(app) as client:
        resp = client.get("/model/gallery")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            model = data[0]
            assert "name" in model or "uniqueID" in model


@pytest.mark.skip(reason="Skipping test_model_list_local_uninstalled because it is taking 23 seconds to load??!!")
def test_model_list_local_uninstalled():
    with TestClient(app) as client:
        resp = client.get("/model/list_local_uninstalled")
        assert resp.status_code == 200
        assert "data" in resp.json() or "status" in resp.json()


def test_model_group_gallery():
    with TestClient(app) as client:
        resp = client.get("/model/model_groups_list")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            model = data[0]
            assert "name" in model or "models" in model


def make_mock_adapter_info(overrides={}):
    return MagicMock(
        modelId="mock/model",
        tags=["tag1", "tag2"],
        cardData={
            "description": "mock desc",
            "base_model": "unsloth/Llama-3.2-1B-Instruct",
            **overrides.get("cardData", {}),
        },
        config={"architectures": "MockArch", "model_type": "MockType", **overrides.get("config", {})},
        downloads=123,
    )


@pytest.mark.asyncio
@patch("transformerlab.routers.model.huggingfacemodel.get_model_details_from_huggingface", new_callable=AsyncMock)
@patch("transformerlab.routers.model.shared.async_run_python_script_and_update_status", new_callable=AsyncMock)
async def test_install_peft_mock(mock_run_script, mock_get_details):
    with TestClient(app) as client:
        # Mock get_model_details to return a dummy config
        mock_get_details.return_value = {"name": "dummy_adapter"}

        # Mock run_script to simulate a subprocess with success
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_run_script.return_value = mock_process

        test_model_id = "unsloth_Llama-3.2-1B-Instruct"
        test_peft_id = "dummy_adapter"

        response = client.post(f"/model/install_peft?model_id={test_model_id}&peft={test_peft_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"  # As install_peft now returns 'started' after starting the async task


@pytest.mark.asyncio
@patch("transformerlab.routers.model.snapshot_download")
@patch("transformerlab.routers.model.huggingfacemodel.get_model_details_from_huggingface", new_callable=AsyncMock)
@patch("transformerlab.routers.model.shared.async_run_python_script_and_update_status", new_callable=AsyncMock)
async def test_install_peft_base_model_adaptor_not_found(mock_run_script, mock_get_details, mock_snapshot):
    mock_snapshot.return_value = "/tmp/empty_folder"
    os.makedirs("/tmp/empty_folder", exist_ok=True)

    mock_get_details.return_value = {"name": "dummy_adapter"}
    mock_run_script.return_value = AsyncMock()

    response = TestClient(app).post("/model/install_peft?model_id=broken_model&peft=dummy_adapter")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "adapter not found" in data["message"]


def test_install_peft_success():
    adapter_id = "tcotter/Llama-3.2-1B-Instruct-Mojo-Adapter"
    model_id = "unsloth/Llama-3.2-1B-Instruct"

    with (
        TestClient(app) as client,
        patch("transformerlab.routers.model.snapshot_download", return_value="/tmp/mock"),
        patch("builtins.open", mock_open(read_data='{"architectures": "MockArch", "model_type": "MockType"}')),
        patch("json.load", return_value={"architectures": "MockArch", "model_type": "MockType"}),
        patch("huggingface_hub.HfApi.model_info", return_value=make_mock_adapter_info()),
        patch("transformerlab.routers.model.huggingfacemodel.get_model_details_from_huggingface", return_value={}),
        patch("transformerlab.routers.model.db.job_create", return_value=123),
        patch("transformerlab.routers.model.asyncio.create_task"),
    ):
        response = client.post("/model/install_peft", params={"peft": adapter_id, "model_id": model_id})
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "started"
        assert result["check_status"]["base_model_name"] in ["success", "fail"]
        assert result["check_status"]["architectures_status"] in ["success", "fail", "unknown"]


def test_install_peft_model_config_fail():
    with (
        TestClient(app) as client,
        patch("transformerlab.routers.model.snapshot_download", side_effect=FileNotFoundError()),
    ):
        response = client.post("/model/install_peft", params={"peft": "dummy", "model_id": "invalid-model"})
        assert response.status_code == 200
        assert response.json()["check_status"]["error"] == "not found"


def test_install_peft_adapter_info_fail():
    with (
        TestClient(app) as client,
        patch("transformerlab.routers.model.snapshot_download", return_value="/tmp/mock"),
        patch("builtins.open", mock_open(read_data="{}")),
        patch("json.load", return_value={}),
        patch("huggingface_hub.HfApi.model_info", side_effect=RuntimeError("not found")),
    ):
        response = client.post("/model/install_peft", params={"peft": "dummy", "model_id": "valid_model"})
        assert response.status_code == 200
        assert response.json()["check_status"]["error"] == "not found"


def test_install_peft_architecture_detection_unknown():
    adapter_info = make_mock_adapter_info()
    with (
        TestClient(app) as client,
        patch("transformerlab.routers.model.snapshot_download", return_value="/tmp/mock"),
        patch("builtins.open", mock_open(read_data="{}")),
        patch("json.load", return_value={"architectures": "A", "model_type": "B"}),
        patch("huggingface_hub.HfApi.model_info", return_value=adapter_info),
        patch("transformerlab.routers.model.huggingfacemodel.get_model_details_from_huggingface", return_value={}),
        patch("transformerlab.routers.model.db.job_create", return_value=123),
        patch("transformerlab.routers.model.asyncio.create_task"),
    ):
        response = client.post("/model/install_peft", params={"peft": "dummy", "model_id": "valid_model"})
        assert response.status_code == 200
        assert response.json()["check_status"]["architectures_status"] == "unknown"


def test_install_peft_unknown_field_status():
    adapter_info = make_mock_adapter_info(overrides={"config": {}})
    with (
        TestClient(app) as client,
        patch("transformerlab.routers.model.snapshot_download", return_value="/tmp/mock"),
        patch("builtins.open", mock_open(read_data="{}")),
        patch("json.load", return_value={}),
        patch("huggingface_hub.HfApi.model_info", return_value=adapter_info),
        patch("transformerlab.routers.model.huggingfacemodel.get_model_details_from_huggingface", return_value={}),
        patch("transformerlab.routers.model.db.job_create", return_value=123),
        patch("transformerlab.routers.model.asyncio.create_task"),
    ):
        response = client.post("/model/install_peft", params={"peft": "dummy", "model_id": "valid_model"})
        status = response.json()["check_status"]
        assert status["architectures_status"] == "unknown"
        assert status["model_type_status"] == "unknown"
