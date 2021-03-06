from datetime import datetime
from typing import Text

import pandas as pd
from rasa.core.tracker_store import MongoTrackerStore, DialogueStateTracker

from .processor import MongoProcessor


class ChatHistory:
    mongo_processor = MongoProcessor()

    @staticmethod
    def get_tracker_and_domain(bot: Text):
        domain = ChatHistory.mongo_processor.load_domain(bot)
        endpoint = ChatHistory.mongo_processor.get_endpoints(bot)
        tracker = MongoTrackerStore(
            domain=domain,
            host=endpoint["tracker_endpoint"]["url"],
            db=endpoint["tracker_endpoint"]["db"],
            username=endpoint["tracker_endpoint"].get("username"),
            password=endpoint["tracker_endpoint"].get("password"),
        )
        return (domain, tracker)

    @staticmethod
    def fetch_chat_history(bot: Text, sender, latest_history=False):
        events = ChatHistory.fetch_user_history(
            bot, sender, latest_history=latest_history
        )
        return list(ChatHistory.__prepare_data(bot, events))

    @staticmethod
    def fetch_chat_users(bot: Text):
        _, tracker = ChatHistory.get_tracker_and_domain(bot)
        return tracker.keys()

    @staticmethod
    def __prepare_data(bot: Text, events, show_session=False):
        bot_action = None
        training_examples, ids = ChatHistory.mongo_processor.get_all_training_examples(bot)
        if events:
            for i in range(events.__len__()):
                event = events[i]
                event_data = event.as_dict()
                if event_data["event"] not in ["action", "rewind"]:
                    result = {
                        "event": event_data["event"],
                        "time": datetime.fromtimestamp(event_data["timestamp"]).time(),
                        "date": datetime.fromtimestamp(event_data["timestamp"]).date(),
                    }

                    if event_data["event"] not in ["session_started", "rewind"]:
                        result["text"] = event_data["text"]
                        result["is_exists"] = event_data["text"] in training_examples
                        if result["is_exists"]:
                            result["_id"] = ids[training_examples.index(event_data["text"])]

                    if event_data["event"] == "user":
                        parse_data = event_data["parse_data"]
                        result["intent"] = parse_data["intent"]["name"]
                        result["confidence"] = parse_data["intent"]["confidence"]
                    elif event_data["event"] == "bot":
                        if bot_action:
                            result["action"] = bot_action

                    if event_data["event"] == "session_started" and not show_session:
                        continue
                    yield result
                else:
                    bot_action = (
                        event_data["name"] if event_data["event"] == "action" else None
                    )

    @staticmethod
    def fetch_user_history(bot: Text, sender_id: Text, latest_history=True):
        domain, tracker = ChatHistory.get_tracker_and_domain(bot)
        if latest_history:
            return tracker.retrieve(sender_id).as_dialogue().events
        else:
            user_conversation = tracker.conversations.find_one({"sender_id": sender_id})
            if user_conversation:
                return (
                    DialogueStateTracker.from_dict(
                        sender_id, list(user_conversation["events"]), domain.slots
                    )
                    .as_dialogue()
                    .events
                )

    @staticmethod
    def visitor_hit_fallback(bot: Text):
        data_frame = ChatHistory.__fetch_history_metrics(bot)
        if data_frame.empty:
            fallback_count = 0
            total_count = 0
        else:
            fallback_count = data_frame[
                data_frame["name"] == "action_default_fallback"
            ].count()["name"]
            total_count = data_frame.count()["name"]
        return {"fallback_count": int(fallback_count), "total_count": int(total_count)}

    @staticmethod
    def conversation_steps(bot: Text):
        """
        data_frame = data_frame.groupby(['sender_id', data_frame.event.ne('session_started')])
        data_frame = data_frame.size().reset_index()
        data_frame.rename(columns={0: 'count'}, inplace=True)
        data_frame = data_frame[data_frame['event'] != 0]
        return data_frame.to_json(orient='records')
        """
        data_frame = ChatHistory.__fetch_history_metrics(bot)
        if data_frame.empty:
            return {}
        else:
            data_frame["prev_event"] = data_frame["event"].shift()
            data_frame["prev_timestamp"] = data_frame["timestamp"].shift()
            data_frame.fillna("", inplace=True)
            data_frame = data_frame[
                ((data_frame["event"] == "bot") & (data_frame["prev_event"] == "user"))
            ]
            return (
                data_frame.groupby(["sender_id"])
                .count()
                .reset_index()[["sender_id", "event"]]
                .to_dict(orient="records")
            )

    @staticmethod
    def conversation_time(bot: Text):
        data_frame = ChatHistory.__fetch_history_metrics(bot)
        if data_frame.empty:
            return {}
        else:
            data_frame["prev_event"] = data_frame["event"].shift()
            data_frame["prev_timestamp"] = data_frame["timestamp"].shift()
            data_frame.fillna("", inplace=True)
            data_frame = data_frame[
                ((data_frame["event"] == "bot") & (data_frame["prev_event"] == "user"))
            ]
            data_frame["time"] = pd.to_datetime(
                data_frame["timestamp"], unit="s"
            ) - pd.to_datetime(data_frame["prev_timestamp"], unit="s")
            return (
                data_frame[["sender_id", "time"]]
                .groupby("sender_id")
                .sum()
                .reset_index()
                .to_dict(orient="records")
            )

    @staticmethod
    def get_conversations(bot: Text):
        _, tracker = ChatHistory.get_tracker_and_domain(bot)
        return list(tracker.conversations.find())

    @staticmethod
    def __fetch_history_metrics(bot: Text, show_session=False, filter_columns=None):
        filter_events = ["user", "bot"]
        if show_session:
            filter_events.append("session_started")

        if not filter_columns:
            filter_columns = [
                "sender_id",
                "event",
                "name",
                "text",
                "timestamp",
                "input_channel",
                "message_id",
            ]
        records = ChatHistory.get_conversations(bot)
        data_frame = pd.DataFrame(list(records))
        if not data_frame.empty:
            data_frame = data_frame.explode(column="events")
            data_frame = pd.concat(
                [
                    data_frame.drop(["events"], axis=1),
                    data_frame["events"].apply(pd.Series),
                ],
                axis=1,
            )
            data_frame.fillna("", inplace=True)
            data_frame["name"] = data_frame["name"].shift()
            data_frame = data_frame[data_frame["event"].isin(filter_events)]
            data_frame = data_frame[filter_columns]
        return data_frame
