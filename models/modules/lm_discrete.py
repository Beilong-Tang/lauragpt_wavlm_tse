import torch.nn as nn
import torch
import math
from .mlp import MLP


def positionalencoding1d(d_model, length):
    """
    :param d_model: dimension of the model
    :param length: length of positions
    :return: length*d_model position matrix
    """
    if d_model % 2 != 0:
        raise ValueError(
            "Cannot use sin/cos positional encoding with "
            "odd dim (got dim={:d})".format(d_model)
        )
    pe = torch.zeros(length, d_model)
    position = torch.arange(0, length).unsqueeze(1)
    div_term = torch.exp(
        (
            torch.arange(0, d_model, 2, dtype=torch.float)
            * -(math.log(10000.0) / d_model)
        )
    )
    pe[:, 0::2] = torch.sin(position.float() * div_term)
    pe[:, 1::2] = torch.cos(position.float() * div_term)
    return pe


class SinuPosEncoding(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, x):
        """
        :param x: [B,T,E]
        :return x
        """
        pe = positionalencoding1d(x.size(-1), x.size(-2))  # [T, E]
        pe = pe.to(x.device)
        return x + pe.unsqueeze(0)


# class SelmEncoderLanguageModel(nn.Module):
#     def __init__(self, d, num, attention_heads, norm_first=False):
#         super().__init__()
#         self.positional_encoding = SinuPosEncoding()
#         encoder_layer = nn.TransformerEncoderLayer(
#             d_model=d, nhead=attention_heads, batch_first=True, norm_first=norm_first
#         )
#         encoder = nn.TransformerEncoder(encoder_layer, num_layers=num)
#         self.transformer = encoder

#     def forward(self, x):
#         """
#         :params x [B,T,E]
#         """
#         x = self.positional_encoding(x)
#         return self.transformer(x)


class _MultiHeadAttention(nn.Module):
    def __init__(self, input_dim, nheads=16, is_causal=True):
        super().__init__()
        self.multi_head = nn.MultiheadAttention(
            input_dim, num_heads=nheads, batch_first=True
        )
        self.is_causal = is_causal
        pass

    def forward(self, query, key, value):
        """[B,T,E]"""
        if self.is_causal:
            seq_length = key.size(1)
            attn_mask = torch.tril(torch.ones(seq_length, seq_length), diagonal=1)
            attn_mask = attn_mask.masked_fill(attn_mask == 0, float("-inf")).to(
                key.device
            )
            attn_out, _ = self.multi_head(query, key, value, attn_mask=attn_mask)
        else:
            attn_out, _ = self.multi_head(query, key, value)
        return attn_out


class MultiHeadSelfAttention(nn.Module):
    def __init__(self, input_dim, nheads=16, is_causal=True):
        super().__init__()
        self.key = nn.Linear(input_dim, input_dim)
        self.query = nn.Linear(input_dim, input_dim)
        self.value = nn.Linear(input_dim, input_dim)
        self.multi_head = _MultiHeadAttention(
            input_dim, nheads=nheads, is_causal=is_causal
        )
        pass

    def forward(self, x):
        """[B,T,E]"""
        key = self.key(x)
        query = self.query(x)
        value = self.value(x)
        return self.multi_head(query, key, value)


class SelmTransformerDecoderLayer(nn.Module):
    def __init__(self, emb_dim=512, nheads=16, hidden_dim=1024, p=0.0, is_causal=True):
        super().__init__()
        print(nheads)
        self.multi = MultiHeadSelfAttention(emb_dim, nheads, is_causal=is_causal)
        self.dropout_1 = nn.Dropout(p)
        self.norm1 = nn.LayerNorm(emb_dim)
        self.mlp = MLP(emb_dim, hidden_dim, emb_dim, activation="nn.GELU")
        print("using selm transformer decoder layer")

    def forward(self, x):
        """[B,L,E]"""
        ## self-attention block
        y = self.norm1(x)  # norm
        y = self.multi(y)  # [B, T, E]
        y = self.dropout_1(y)
        y = x + y  # [B, T, E]
        ## mlp block
        y1 = self.mlp(y)
        y = y + y1
        return y


class SelmTransformerDecoderCrossAttentionLayer(nn.Module):
    def __init__(self, emb_dim=512, nheads=16, hidden_dim=1024, p=0.0, is_causal=True):
        super().__init__()
        print(nheads)
        self.multi = _MultiHeadAttention(emb_dim, nheads, is_causal=is_causal)
        self.dropout_1 = nn.Dropout(p)
        self.norm1 = nn.LayerNorm(emb_dim)
        self.mlp = MLP(emb_dim, hidden_dim, emb_dim, activation="nn.GELU")
        self.query = nn.Linear(emb_dim, emb_dim)
        self.key = nn.Linear(emb_dim, emb_dim)
        self.value = nn.Linear(emb_dim, emb_dim)
        print("using selm transformer decoder cross attention layer")

    def forward(self, x, regi):
        """x:[mixed embedding], regi: [regi embedding]"""
        ## cross-attention block
        y = self.norm1(x)  # norm
        query = self.query(regi)
        key = self.key(y)
        value = self.value(y)
        y = self.multi(query, key, value)  # [B, T, E]
        y = self.dropout_1(y)
        y = x + y  # [B, T, E]
        ## mlp block
        y1 = self.mlp(y)
        y = y + y1
        return y


class CrossAttentionDecoderBlock(nn.Module):
    def __init__(
        self,
        d_model=512,
        num_layers=1,
        num_layers_regi=1,
        nheads=16,
        is_causal=True,
        cross_first=False,
        hidden_dim=1024,
    ):
        super().__init__()
        if num_layers == 0:
            layers = [nn.Identity()]
        else:
            layers = [
                SelmTransformerDecoderLayer(
                    d_model, nheads, hidden_dim, is_causal=is_causal
                )
                for _ in range(0, num_layers - 1)
            ]
        if num_layers_regi == 0:
            layers_regi = [nn.Identity()]
        else:
            layers_regi = [
                SelmTransformerDecoderLayer(
                    d_model, nheads, hidden_dim, is_causal=is_causal
                )
                for _ in range(0, num_layers_regi - 1)
            ]
        self.cross = SelmTransformerDecoderCrossAttentionLayer(
            d_model, nheads, is_causal=is_causal
        )
        self.layers = nn.Sequential(*layers)
        self.layers_regi = nn.Sequential(*layers_regi)
        self.cross_first = cross_first

    def forward(self, args):
        """args:(x : [B,T, E], regi: [B,T, E]) -> (x: [B, T, E] regi:[B, T, E])"""
        x = args[0]
        regi = args[1]
        if self.cross_first:
            x = self.cross(x, regi)
            regi = self.layers_regi(regi)
            x = self.layers(x)
        else:
            x = self.layers(x)  # [B,T,E]
            regi = self.layers_regi(regi)
            x = self.cross(x, regi)
        return (x, regi)


class LanguageModel(nn.Module):
    def __init__(
        self,
        emb_num=1000,
        emb_dim=512,
        nheads=16,
        hidden_dim=1024,
        num=4,
        is_causal=True,
        cross_first=False,
    ):
        super().__init__()
        self.positional_encoding = SinuPosEncoding()
        layers = [
            CrossAttentionDecoderBlock(
                d_model=emb_dim,
                nheads=nheads,
                hidden_dim=hidden_dim,
                is_causal=is_causal,
                cross_first=cross_first,
            )
            for _ in range(0, num)
        ]
        self.layers = nn.Sequential(*layers)
        self.audio_embedding = nn.Embedding(emb_num, emb_dim)
        self.regi_embedding = nn.Embedding(emb_num, emb_dim)
        self.classifier = nn.Linear(emb_dim, emb_num)
        print("Using selm transofmer decoder cross attention.....")

    def forward(self, x, register):
        """[B,T], [B,T] -> [B, T, E]"""
        x = self.audio_embedding(x)  # [B, T, E]
        register = self.regi_embedding(register)  # [B, T, E]
        y = self.positional_encoding(x)
        register = self.positional_encoding(register)  # [B, T, E]
        y, _ = self.layers((y, register))
        y = self.classifier(y)  # [B, T, D]
        y = y.transpose(1, 2)
        return y


if __name__ == "__main__":
    ### encoding
    emb = torch.randint(0, 200, (2, 50))
    x = torch.randint(0, 200, (2, 50))
    model = LanguageModel(num=1)
    output = model(x, emb)
    print(output.shape)

    pass
