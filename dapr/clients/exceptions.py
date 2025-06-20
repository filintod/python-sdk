# -*- coding: utf-8 -*-

"""
Copyright 2023 The Dapr Authors
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import base64
import json
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from dapr.serializers import Serializer

from google.protobuf.json_format import MessageToDict
from grpc import RpcError  # type: ignore
from grpc_status import rpc_status  # type: ignore
from google.rpc import error_details_pb2  # type: ignore

ERROR_CODE_UNKNOWN = 'UNKNOWN'
ERROR_CODE_DOES_NOT_EXIST = 'ERR_DOES_NOT_EXIST'


class DaprInternalError(Exception):
    """DaprInternalError encapsulates all Dapr exceptions"""

    def __init__(
        self,
        message: Optional[str],
        error_code: Optional[str] = ERROR_CODE_UNKNOWN,
        raw_response_bytes: Optional[bytes] = None,
    ):
        self._message = message
        self._error_code = error_code
        self._raw_response_bytes = raw_response_bytes

    def as_dict(self):
        return {
            'message': self._message,
            'errorCode': self._error_code,
            'raw_response_bytes': self._raw_response_bytes,
        }

    def as_json_safe_dict(self):
        error_dict = self.as_dict()

        if self._raw_response_bytes is not None:
            # Encode bytes to base64 for JSON compatibility
            error_dict['raw_response_bytes'] = base64.b64encode(self._raw_response_bytes).decode(
                'utf-8'
            )

        return error_dict

    @property
    def message(self) -> Optional[str]:
        """Get the error message"""
        return self._message

    @property
    def error_code(self) -> Optional[str]:
        """Get the error code"""
        return self._error_code

    @property
    def raw_response_bytes(self) -> Optional[bytes]:
        """Get the raw response bytes"""
        return self._raw_response_bytes

    def __str__(self):
        if self._error_code != ERROR_CODE_UNKNOWN:
            return f"('{self._message}', '{self._error_code}')"
        return self._message or 'Unknown Dapr Error.'


class StatusDetails:
    def __init__(self):
        self.error_info = None
        self.retry_info = None
        self.debug_info = None
        self.quota_failure = None
        self.precondition_failure = None
        self.bad_request = None
        self.request_info = None
        self.resource_info = None
        self.help = None
        self.localized_message = None

    def as_dict(self):
        return {attr: getattr(self, attr) for attr in self.__dict__}


class DaprHttpError(DaprInternalError):
    """DaprHttpError encapsulates all Dapr HTTP exceptions

    Attributes:
        _status_code: HTTP status code
        _reason: HTTP reason phrase
    """

    def __init__(
        self,
        serializer: 'Serializer',
        raw_response_bytes: Optional[bytes] = None,
        status_code: Optional[int] = None,
        reason: Optional[str] = None,
    ):
        self._status_code = status_code
        self._reason = reason
        error_code: str = ERROR_CODE_UNKNOWN
        message: Optional[str] = None
        error_info: Optional[dict] = None

        if (raw_response_bytes is None or len(raw_response_bytes) == 0) and status_code == 404:
            error_code = ERROR_CODE_DOES_NOT_EXIST
            raw_response_bytes = None
        elif raw_response_bytes:
            try:
                error_info = serializer.deserialize(raw_response_bytes)
            except Exception:
                pass
                # ignore any errors during deserialization

            if error_info and isinstance(error_info, dict):
                message = error_info.get('message')
                error_code = error_info.get('errorCode') or ERROR_CODE_UNKNOWN

        super().__init__(
            message or f'HTTP status code: {status_code}', error_code, raw_response_bytes
        )

    @property
    def status_code(self) -> Optional[int]:
        return self._status_code

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    def as_dict(self):
        error_dict = super().as_dict()
        error_dict['status_code'] = self._status_code
        error_dict['reason'] = self._reason
        return error_dict

    def __str__(self):
        if self._error_code != ERROR_CODE_UNKNOWN:
            return f'{self._message} (Error Code: {self._error_code}, Status Code: {self._status_code})'
        else:
            return f'Unknown Dapr Error. HTTP status code: {self._status_code}.'


class DaprGrpcError(RpcError):
    def __init__(self, err: RpcError):
        self._status_code = err.code()
        self._err_message = err.details()
        self._details = StatusDetails()

        self._grpc_status = rpc_status.from_call(err)
        self._parse_details()

    def _parse_details(self):
        if self._grpc_status is None:
            return

        for detail in self._grpc_status.details:
            if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
                self._details.error_info = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.RetryInfo.DESCRIPTOR):
                self._details.retry_info = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.DebugInfo.DESCRIPTOR):
                self._details.debug_info = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.QuotaFailure.DESCRIPTOR):
                self._details.quota_failure = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.PreconditionFailure.DESCRIPTOR):
                self._details.precondition_failure = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.BadRequest.DESCRIPTOR):
                self._details.bad_request = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.RequestInfo.DESCRIPTOR):
                self._details.request_info = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.ResourceInfo.DESCRIPTOR):
                self._details.resource_info = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.Help.DESCRIPTOR):
                self._details.help = serialize_status_detail(detail)
            elif detail.Is(error_details_pb2.LocalizedMessage.DESCRIPTOR):
                self._details.localized_message = serialize_status_detail(detail)

    def code(self):
        return self._status_code

    def details(self):
        """
        We're keeping the method name details() so it matches the grpc.RpcError interface.
        @return:
        """
        return self._err_message

    def error_code(self):
        if not self.status_details() or not self.status_details().error_info:
            return ERROR_CODE_UNKNOWN
        return self.status_details().error_info.get('reason', ERROR_CODE_UNKNOWN)

    def status_details(self):
        return self._details

    def get_grpc_status(self):
        return self._grpc_status

    def json(self):
        error_details = {
            'status_code': self.code().name,
            'message': self.details(),
            'error_code': self.error_code(),
            'details': self._details.as_dict(),
        }
        return json.dumps(error_details)


def serialize_status_detail(status_detail):
    if not status_detail:
        return None
    return MessageToDict(status_detail, preserving_proto_field_name=True)
