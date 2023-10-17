# Copyright (c) Hegel AI, Inc.
# All rights reserved.
#
# This source code's license can be found in the
# LICENSE file in the root directory of this source tree.
import copy
import os
import json
import pickle
from typing import Dict, List, Optional, Union
import openai
import requests
import itertools
import logging

from prompttools.selector.prompt_selector import PromptSelector
from prompttools.mock.mock import mock_openai_chat_completion_fn, mock_openai_chat_function_completion_fn
from .experiment import Experiment
from .error import PromptExperimentException
import pandas as pd


class OpenAIChatExperiment(Experiment):
    r"""
    This class defines an experiment for OpenAI's chat completion API.
    It accepts lists for each argument passed into OpenAI's API, then creates
    a cartesian product of those arguments, and gets results for each.

    Note:
        - All arguments here should be a ``list``, even if you want to keep the argument frozen
          (i.e. ``temperature=[1.0]``), because the experiment will try all possible combination
          of the input arguments.
        - For detailed description of the input arguments, please reference at OpenAI's chat completion API.

    Args:
        model (list[str]): list of ID(s) of the model(s) to use, e.g. ``["gpt-3.5-turbo", "ft:gpt-3.5-turbo:org_id"]``
            If you are using Azure OpenAI service, put the models' deployment names here

        messages (list[dict]): A list of messages comprising the conversation so far. Each message is represented as a
            dictionary with the following keys: ``role: str``, ``content: str``.

        temperature (list[float]):
            Defaults to [1.0]. What sampling temperature to use, between 0 and 2. Higher values like 0.8 will make
            the output more random, while lower values like 0.2 will make it more focused and deterministic.

        top_p (list[float]):
            Defaults to [1.0]. An alternative to sampling with temperature, called nucleus sampling, where the
            model considers the results of the tokens with top_p probability mass. So 0.1 means only the tokens
            comprising the top 10% probability mass are considered.

        n (list[int]):
            Defaults to [1]. How many chat completion choices to generate for each input message.

        stream (list[bool]):
            Defaults to [False]. If set, partial message deltas will be sent, like in ChatGPT. Tokens will be sent
            as data-only server-sent events as they become available, with the stream terminated by a data: [DONE]
            message.

        stop (list[list[str]]):
            Defaults to [None]. Up to 4 sequences where the API will stop generating further tokens.

        max_tokens (list[int]):
            Defaults to [inf]. The maximum number of tokens to generate in the chat completion.

        presence_penalty (list[float]):
            Defaults to [0.0]. Number between -2.0 and 2.0. Positive values penalize new tokens based on whether
            they appear in the text so far, increasing the model's likelihood to talk about new topics.

        frequency_penalty (list[float]):
            Defaults to [0.0]. Number between -2.0 and 2.0. Positive values penalize new tokens based on their
            existing frequency in the text so far, decreasing the model's likelihood to repeat the same line
            verbatim.

        logit_bias (list[dict]):
            Defaults to [None]. Modify the likelihood of specified tokens appearing in the completion. Accepts a
            json object that maps tokens (specified by their token ID in the tokenizer) to an associated bias value
            from -100 to 100.

        functions (list[dict]):
            Defaults to [None]. A list of dictionaries, each of which contains the definition of a function
            the model may generate JSON inputs for.

        function_call (list[dict]):
            Defaults to [None]. A dictionary containing the name and arguments of a function that should be called,
            s generated by the model.

        azure_openai_service_configs (Optional[dict]):
            Defaults to ``None``. If it is set, the experiment will use Azure OpenAI Service. The input dict should
            contain these 3 keys (but with values based on your use case and configuration):
            ``{"AZURE_OPENAI_ENDPOINT": "https://YOUR_RESOURCE_NAME.openai.azure.com/",
               "API_TYPE": "azure", "API_VERSION": "2023-05-15"``
    """

    def __init__(
        self,
        model: List[str],
        messages: Union[List[List[Dict[str, str]]], List[PromptSelector]],
        temperature: Optional[List[float]] = [1.0],
        top_p: Optional[List[float]] = [1.0],
        n: Optional[List[int]] = [1],
        stream: Optional[List[bool]] = [False],
        stop: Optional[List[List[str]]] = [None],
        max_tokens: Optional[List[int]] = [float("inf")],
        presence_penalty: Optional[List[float]] = [0.0],
        frequency_penalty: Optional[List[float]] = [0.0],
        logit_bias: Optional[List[Dict]] = [None],
        functions: Optional[List[Dict]] = [None],
        function_call: Optional[List[Dict[str, str]]] = [None],
        azure_openai_service_configs: Optional[dict] = None,
    ):
        self.completion_fn = openai.ChatCompletion.create
        if os.getenv("DEBUG", default=False):
            if functions[0] is not None:
                self.completion_fn = mock_openai_chat_function_completion_fn
            else:
                self.completion_fn = mock_openai_chat_completion_fn

        # If we are using a prompt selector, we need to render
        # messages, as well as create prompt_keys to map the messages
        # to corresponding prompts in other models.
        if isinstance(messages[0], PromptSelector):
            self.prompt_keys = {
                str(selector.for_openai_chat()[-1]["content"]): selector.for_llama() for selector in messages
            }
            messages = [selector.for_openai_chat() for selector in messages]
        else:
            self.prompt_keys = messages

        self.all_args = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            functions=functions,
            function_call=function_call,
            top_p=top_p,
            n=n,
            stream=stream,
            stop=stop,
            max_tokens=max_tokens,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            logit_bias=logit_bias,
        )

        # These parameters aren't supported by `gpt-35-turbo`, we can remove them if they are equal to defaults
        # This has no impact on the default case
        if self.all_args["logit_bias"] == [None]:
            del self.all_args["logit_bias"]

        if azure_openai_service_configs:
            openai.api_key = os.environ["AZURE_OPENAI_KEY"]
            openai.api_base = azure_openai_service_configs["AZURE_OPENAI_ENDPOINT"]
            openai.api_type = azure_openai_service_configs["API_TYPE"]
            openai.api_version = azure_openai_service_configs["API_VERSION"]
            del self.all_args["model"]
            self.all_args["engine"] = model

        super().__init__()

    @staticmethod
    def _extract_responses(output: Dict[str, object]) -> str:
        message = output["choices"][0]["message"]
        if "function_call" in message:
            return json.dumps(json.loads(message["function_call"]["arguments"]))
        else:
            return message["content"]

    @staticmethod
    def _is_chat():
        return True

    def _get_model_names(self):
        return [combo["model"] for combo in self.argument_combos]

    def _get_prompts(self):
        return [self.prompt_keys[str(combo["messages"][-1]["content"])] for combo in self.argument_combos]

    def _get_state(self, name: str):
        state_params = {
            "prompt_keys": self.prompt_keys,
            "all_args": self.all_args,
        }
        partial_col_names = self.partial_df.columns.tolist()
        score_col_names = self.score_df.columns.tolist()
        state = (
            name,
            self._experiment_id,
            state_params,
            self.full_df,
            partial_col_names,
            score_col_names,
        )
        print("Creating serialized state of experiment...")
        serialized_state = pickle.dumps(state)
        return serialized_state

    def save_experiment(self, name: str):
        if os.environ["HEGELAI_API_KEY"] is None:
            raise PermissionError("Please set HEGELAI_API_KEY (e.g. os.environ['HEGELAI_API_KEY']).")
        state = self._get_state(name)
        url = "http://127.0.0.1:5000/experiment/save"
        headers = {
            "Content-Type": "application/octet-stream",  # Use a binary content type for pickled data
            "Authorization": os.environ["HEGELAI_API_KEY"],
        }
        print("Sending HTTP POST request...")
        response = requests.post(url, data=state, headers=headers)
        self._experiment_id = response.json().get("experiment_id")
        return response

    @classmethod
    def load_experiment(cls, experiment_id: str):
        if os.environ["HEGELAI_API_KEY"] is None:
            raise PermissionError("Please set HEGELAI_API_KEY (e.g. os.environ['HEGELAI_API_KEY']).")

        url = f"http://127.0.0.1:5000/experiment/load/{experiment_id}"
        headers = {
            "Content-Type": "application/octet-stream",  # Use a binary content type for pickled data
            "Authorization": os.environ["HEGELAI_API_KEY"],
        }
        print("Sending HTTP GET request...")
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            state = pickle.loads(response.content)  # Note that state should not have `name` included
            return cls._load_state(state, experiment_id)
        else:
            print(f"Error: {response.status_code}, {response.text}")

    @classmethod
    def _load_state(cls, state, experiment_id: str):
        (
            state_params,
            full_df,
            partial_col_names,
            score_col_names,
        ) = state

        all_args, prompt_keys = state_params["all_args"], state_params["prompt_keys"]
        experiment = cls(all_args["model"], all_args["messages"])
        experiment.prompt_keys = prompt_keys
        experiment.all_args = all_args
        experiment.full_df = pd.DataFrame(full_df)
        experiment.partial_df = experiment.full_df[partial_col_names].copy()
        experiment.score_df = experiment.full_df[score_col_names].copy()
        experiment._experiment_id = experiment_id
        print("Loaded experiment.")
        return experiment

    def _validate_arg_key(self, arg_name: str) -> None:
        import inspect

        signature = inspect.signature(self.__init__)
        name_exceptions = {"azure_openai_service_configs"}

        if arg_name in [param.name for param in signature.parameters.values()] and arg_name not in name_exceptions:
            return
        else:
            raise RuntimeError("Provided argument name does not match known argument names.")

    def run_partial(self, **kwargs):
        r"""
        Run experiment with against one parameter, which can be existing or new. The new result will
        be appended to any existing DataFrames.

        If the argument value did not exist before, it will be added to the list of argument combinations
        that will be executed in the next run.

        e.g. `experiement.run_partial({model: 'gpt-4'})`
        """
        print("Running partial experiment...")
        if len(kwargs) > 1:
            raise RuntimeError("Not supported.")
        arg_name, arg_value = list(kwargs.items())[0]

        partial_all_args = copy.deepcopy(self.all_args)
        partial_all_args[arg_name] = [arg_value]

        partial_argument_combos = [
            dict(zip(partial_all_args, val)) for val in itertools.product(*partial_all_args.values())
        ]
        original_n_results = len(self.queue.get_results())

        # Execute partial experiment
        for combo in partial_argument_combos:
            self.queue.enqueue(
                self.completion_fn,
                # We need to filter out defaults that are invalid JSON from the request
                {k: v for k, v in combo.items() if (v is not None) and (v != float("inf"))},
            )

        # Verify new results are added
        if original_n_results - len(self.queue.get_results()) == 0:
            logging.error("No results. Something went wrong.")
            raise PromptExperimentException

        # Currently, it always append new rows to the results.
        # In the future, we may want to replace existing rows instead.
        self._construct_result_dfs(self.queue.get_input_args(), self.queue.get_results(), self.queue.get_latencies())

        # If `arg_value` didn't exist before, add to `argument_combos`, which will be used in the next `.run()`
        if arg_value not in self.all_args[arg_name]:
            self.all_args[arg_name].append(arg_value)
            self.prepare()

    # def _update_values_in_dataframe(self):
    #     r"""
    #     If, in the future, we wish to update existing values rather than appending to the end of the row.
    #
    #     # Consider doing a merge left here
    #     #       1. Identify what input_args columns exist
    #     #       2. Use those columns names for pandas to do a merge left
    #     #       3. If a value (from evals mostly) doesn't exist in the new one, put as NaN or empty
    #     #       4. If 1 has the key combo but 2 doesn't, mkae sure to keep the one from 1
    #     #       5. Make sure `scores_df` is correct
    #     # Alternatively, find the index and overwrite those DataFrame rows, where each row is a `pd.Series`.
    #     """
    #     pass
