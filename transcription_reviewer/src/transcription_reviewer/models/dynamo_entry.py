from dataclasses import dataclass


@dataclass
class DynamoDBMediaEntry:
    media_id: int | str
    media_url: str
    context_files_bucket_s3: str
    media_transcribed_bucket: str
    media_fixed_transcribed_bucket: str
    media_subtitles: str
    media_bucket_s3: str | None = None
    media_statistics: str | None = None
    status: str | None = None
    created_at: str | None = None
    language: str | None = None
    details: str | None = None
    massechet_name: str | None = None
    daf_name: str | None = None
    maggid_description: str | None = None
    media_duration: int | None = None
    source: str | None = None

    @classmethod
    def from_dynamo_item(cls, item: dict) -> "DynamoDBMediaEntry":
        return cls(
            media_id=item["media_id"]["S"],
            media_url=item["media_url"]["S"],
            context_files_bucket_s3=item["context_files_bucket_s3"]["S"],
            media_transcribed_bucket=item["media_transcribed_bucket"]["S"],
            media_fixed_transcribed_bucket=item["media_fixed_transcribed_bucket"]["S"],
            media_subtitles=item["media_subtitles"]["S"],
            media_bucket_s3=item.get("media_bucket_s3", {}).get("S"),
            media_statistics=item.get("media_statistics", {}).get("S"),
            status=item.get("status", {}).get("S"),
            created_at=item.get("created_at", {}).get("S"),
            language=item.get("language", {}).get("S"),
            details=item.get("details", {}).get("S"),
            massechet_name=item.get("massechet_name", {}).get("S"),
            daf_name=item.get("daf_name", {}).get("S"),
            maggid_description=item.get("maggid_description", {}).get("S"),
            media_duration=int(item["media_duration"]["N"]) if "media_duration" in item else None,
            source=item.get("source", {}).get("S"),
        )

    def to_dynamo_item(self) -> dict:
        item: dict = {
            "media_id": {"S": str(self.media_id)},
            "media_url": {"S": self.media_url},
            "context_files_bucket_s3": {"S": self.context_files_bucket_s3},
            "media_transcribed_bucket": {"S": self.media_transcribed_bucket},
            "media_fixed_transcribed_bucket": {"S": self.media_fixed_transcribed_bucket},
            "media_subtitles": {"S": self.media_subtitles},
        }
        if self.media_bucket_s3 is not None:
            item["media_bucket_s3"] = {"S": self.media_bucket_s3}
        if self.media_statistics is not None:
            item["media_statistics"] = {"S": self.media_statistics}
        if self.status is not None:
            item["status"] = {"S": self.status}
        if self.created_at is not None:
            item["created_at"] = {"S": self.created_at}
        if self.language is not None:
            item["language"] = {"S": self.language}
        if self.details is not None:
            item["details"] = {"S": self.details}
        if self.massechet_name is not None:
            item["massechet_name"] = {"S": self.massechet_name}
        if self.daf_name is not None:
            item["daf_name"] = {"S": self.daf_name}
        if self.maggid_description is not None:
            item["maggid_description"] = {"S": self.maggid_description}
        if self.media_duration is not None:
            item["media_duration"] = {"N": str(self.media_duration)}
        if self.source is not None:
            item["source"] = {"S": self.source}
        return item
