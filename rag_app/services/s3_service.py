import os
import boto3
from botocore.exceptions import ClientError
from django.conf import settings


class S3Service:
    def __init__(self):
        self.bucket_name = os.getenv('S3_BUCKET_NAME', '')
        self.region = os.getenv('S3_REGION', None)
        self.access_key = os.getenv('S3_ACCESS_KEY', '')
        self.secret_key = os.getenv('S3_SECRET_KEY', '')
        self.endpoint_url = os.getenv('S3_ENDPOINT_URL', '')

        if self.bucket_name and self.access_key and self.secret_key:
            kwargs = {
                'service_name': 's3',
                'aws_access_key_id': self.access_key,
                'aws_secret_access_key': self.secret_key,
            }
            if self.endpoint_url:
                kwargs['endpoint_url'] = self.endpoint_url
            if self.region:
                kwargs['region_name'] = self.region

            self.s3_client = boto3.client(**kwargs)
        else:
            self.s3_client = None

    def is_configured(self):
        """Check if S3 is properly configured"""
        return bool(self.bucket_name and self.s3_client)

    def upload_file(self, file_path, s3_key=None):
        """
        Upload a file to S3

        Args:
            file_path: Local file path
            s3_key: S3 object key (if None, uses filename)

        Returns:
            S3 URL if successful, None otherwise
        """
        if not self.is_configured():
            return None

        if s3_key is None:
            s3_key = os.path.basename(file_path)

        try:
            self.s3_client.upload_file(file_path, self.bucket_name, s3_key)
            # Return S3 URL
            if self.endpoint_url:
                return f"{self.endpoint_url}/{self.bucket_name}/{s3_key}"
            elif self.region:
                return f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{s3_key}"
            return s3_key
        except ClientError as e:
            print(f"S3 upload error: {e}")
            return None

    def download_file(self, s3_key, local_path):
        """
        Download a file from S3

        Args:
            s3_key: S3 object key
            local_path: Local file path to save

        Returns:
            True if successful, False otherwise
        """
        if not self.is_configured():
            return False

        try:
            self.s3_client.download_file(self.bucket_name, s3_key, local_path)
            return True
        except ClientError as e:
            print(f"S3 download error: {e}")
            return False

    def delete_file(self, s3_key):
        """
        Delete a file from S3

        Args:
            s3_key: S3 object key

        Returns:
            True if successful, False otherwise
        """
        if not self.is_configured():
            return False

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            print(f"S3 delete error: {e}")
            return False


# Singleton instance
s3_service = S3Service()
