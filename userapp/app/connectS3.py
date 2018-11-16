import boto3
import urllib3.request as r
from flask import request
import io

from app.config import aws_config_arg,s3_config_arg


def upload_file_to_s3(filepath, bucket_name, filename, usr_id, acl="public-read"):
    try:
        s3 = boto3.client('s3', **aws_config_arg)
        filename = str(usr_id) + "/" + filename
        s3.upload_file(
            filepath,
            bucket_name,
            filename,
            ExtraArgs={
                "ACL": acl
            }
        )
    except Exception as e:
        print("Something Happened: ", e)
        return e
    return "{}{}".format(s3_config_arg['S3_LOCATION'], filename)
