# RL agents

import gym
import numpy as np
from typing import List
import os
import torch
from torch.distributions import Normal, Categorical
import sys

from rlkits.models import MLP, MLP2heads
from rlkits.env_batch import SpaceBatch


class RandomPolicyWithValue:
    def __init__(self, ob_space, ac_space):
        """
        ob_space: gym env observation space
        ac_space: gym env action space
        """
        self.ob_space = ob_space
        self.ob_dim = len(ob_space.shape)

        self.ac_space = ac_space
        self.ac_dim = len(ac_space.shape)

    def step(self, x):
        return self.take_action(x), self.predict_state_value(x)

    def take_action(self, x):
        return self.ac_space.sample(), 0.5

    def predict_state_value(self, x):
        return np.random.rand()



class PolicyWithValue:
    def __init__(self, ob_space, ac_space, ckpt_dir, **network_kwargs):
        """
        ob_space: gym env observation space
        ac_space: gym env action space
        """
        self.ob_space = ob_space
        self.ob_dim = len(ob_space.shape)

        self.ac_space = ac_space
        self.ac_dim = len(ac_space.shape)
        
        self.ckpt_dir = ckpt_dir
        
        if isinstance(ob_space, SpaceBatch):
            # parallel env 
            self.n = ob_space.sample().shape[0]
        else:
            # single env
            self.n = 1
        
        if isinstance(ac_space, SpaceBatch):
            ac_space_type = type(ac_space.spaces[0])
        else:
            ac_space_type = type(ac_space)
            
        self.input_dim = np.prod(self.ob_space.shape).item()
        
        if ac_space_type is gym.spaces.Box:
            self.output_dim = np.prod(self.ac_space.shape).item()
        elif ac_space_type is gym.spaces.Discrete:
            self.output_dim = ac_space.n
        else:
            raise NotImplemented

        self.continuous = False  # continuous action space
        if self.ac_space.dtype == np.float32:
            self.continuous = True

        if self.continuous:
            print('Continous action space')
            # output is the mean and std of a Gaussian dist
            self.policy_net = MLP(
                input_shape=self.input_dim, output_shape=2, **network_kwargs
            )
        else:
            print('Discrete action space')
            # output is the input of a categorical probability dist
            self.policy_net = MLP(
                input_shape=self.input_dim,
                output_shape=self.output_dim,
                **network_kwargs,
            )

        self.value_net = MLP(
            input_shape=self.input_dim, output_shape=1, **network_kwargs
        )
        
    def average_weight(self):
        """Get average weight of the policy and value net"""
        pi = 0.0 
        cnt = 0
        for p in self.policy_net.parameters():
            pi+= torch.mean(p.data)
            cnt+=1
        pi /= cnt
        
        v = 0.0
        cnt = 0
        for p in self.value_net.parameters():
            v+= torch.mean(p.data)
            cnt+=1
        v /= cnt
        return pi, v
        
        
    def transform_input(self, x):   
        if len(x.shape) == 1:
            x = np.expand_dims(x, axis=0)
        x = torch.from_numpy(x).float()
        return x

    def step(self, x):
        ac, log_prob = self.take_action(x)
        return ac, log_prob, self.predict_state_value(x)

    def take_action(self, x):
        """Take action at the current state of the env"""
        x = self.transform_input(x)
        with torch.no_grad():
            y = self.policy_net(x)
            dist = self.dist(y)
            
        if dist is None:
            print("Policy net blows up -- Bad")
            self.save_ckpt()
            
        action = dist.sample()
        log_prob = dist.log_prob(action)
        return (
            action.numpy(), log_prob.numpy()
        )

    def predict_state_value(self, x):
        """Predict the state value at the current state of the env"""
        x = self.transform_input(x)
        with torch.no_grad():
            v = self.value_net(x)
        return v.numpy().squeeze(axis=1)
    

    def dist(self, params):
        """Get a distribution of actions"""
        if self.continuous:
            assert params.shape[-1] == 2  # mean and log of std
            mean, logstd = torch.split(params, [1, 1], dim=1)
            try: 
                m = Normal(mean, torch.exp(logstd))
                return m
            except Exception as e:
                print(e)
                self.save_ckpt('dead')
                sys.exit()
        else:
            try:
                # apply softmax to the output
                prob = torch.softmax(params, dim=-1)
                m = Categorical(prob)
                return m
            except Exception as e:
                print(e)
                self.save_ckpt('dead')
                sys.exit()
                

    def save_ckpt(self, postfix=''):
        if not os.path.exists(self.ckpt_dir):
            os.makedirs(self.ckpt_dir)

        ckpt = {
            "policy_net": self.policy_net.state_dict(),
            "value_net": self.value_net.state_dict(),
        }

        torch.save(ckpt, os.path.join(self.ckpt_dir, f"ckpt-{postfix}.pth"))
        return

    def load_ckpt(self, ckptfile):
        ckpt = torch.load(ckptfile)
        self.policy_net.load_state_dict(ckpt["policy_net"])
        self.value_net.load_state_dict(ckpt["value_net"])
        return

    
